"""M-33 prediksjons- og scenarioagentens API (migrasjon 130).

Elleve endepunkter: fem leseveier og seks skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_prognose_eier`-eid SECURITY DEFINER-dør i 130, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM ANSETTER, SIER OPP ELLER FLYTTER EN VAKT.

Katalogens vaktsetning er «prognoser er ikke fakta; ingen
personalavgjørelse eller automatisk handling uten separat policy», og
modulen holder den i datamodellen: det finnes ingen tabell for
beslutninger, ingen statuskolonne som kan bli `iverksatt`, og ingen
kolonne som peker på en ansatt. Modulen lager en bane og stopper der.

KLYNGENS DELTE DOM: EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG
PROGNOSE — helt til horisonten er passert, og da har alle sluttet å
se. Derfor er `prognose_uten_maaling` et funn ingen kan lukke, og
`POST /maaling` er den ENESTE veien til å lukke det.

M-33s EGEN DOM: EN MODELL SOM IKKE KAN TAPE, HAR IKKE VUNNET.

FEM NEKT SOM ER VERDT Å KJENNE:

  * `POST /prognose` NEKTER uten tenantens grenser. Uten horisonten
    finnes det ingen dato å måle mot, og da er `prognose_uten_maaling`
    et funn som aldri kan reises.

  * `POST /prognose` NEKTER mot en avviklet modellversjon. Arkivet tar
    imot den; det er BRUKEN som er stengt.

  * `POST /prognose` NEKTER PÅ TOM HISTORIKK, og det er den viktigste.
    En tenant uten en eneste timeregistrering ville fått
    `forventet_minutter = 0` av et snitt over ingenting — og «null
    timer neste uke» fordi ingen har ført timer, er den reneste
    formen for `prognose_presentert_som_faktum`. NULL ARBEID ER IKKE
    DET SAMME SOM INGEN DATA.

  * `POST /maaling` NEKTER en uke som ikke er over. Målingen kan ikke
    rettes, så et delresultat registrert som endelig ville stått for
    alltid. MEN ET GJENSPILL MED SAMME TALL ER IKKE ET NEKT (131): en
    klient som mistet svaret og prøver igjen, får den lagrede raden.
    Å svare 400 der ville fortalt at en skriving som LYKTES hadde
    feilet — og siden dette er den eneste veien til å lukke
    `prognose_uten_maaling`, kunne kalleren ikke engang se hva som
    skjedde.

  * `POST /funn/{id}/lukk` NEKTER på `prognose_uten_maaling` og
    `slaar_ikke_naiv_baseline`. De to lukkes av at tilstanden
    opphører, ikke av at noen huker av.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `okonomi:read` og ikke `security:read`, og det er ikke
en forglemmelse: grunnlaget er `timeregistrering` (M-39, 113), som
allerede leses med `okonomi:read` av lønnsmodulen. Et strengere scope
her ville skjult et AGGREGAT for noen som ser hver enkelt rad — altså
en grense som ser ut som vern og ikke er det.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_PROGNOSER = 200
MAKS_FUNN = 200
MAKS_TEKST = 4000
MAKS_NAVN = 500
#: Metoden må være SKREVET. Seksten tegn er ikke en kvalitetsgaranti —
#: det er en terskel mot «AI» som eneste beskrivelse av en modell noen
#: skal etterprøve.
MIN_TEKST = 16
#: Minutter i en uke for én organisasjon. Et tall over dette er en
#: tastefeil, ikke en arbeidsuke.
MAKS_MINUTTER = 10**9

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 130.
KRAVGRENSER = {
    "horisont_uker": (1, 52),
    # MINST TO, og det er en dom og ikke en slurvegrense: med én
    # observert uke ER snittet forrige uke, og da er modellen identisk
    # med basislinjen sin. En modell som er sin egen basislinje kan
    # ikke tape, og et funn som ikke kan reises er ikke et funn.
    "grunnlag_uker": (2, 104),
    "maalefrist_dogn": (1, 180),
    "domsgrunnlag_uker": (2, 52),
}

_M33_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL,
                        "disponit:m33:prognose")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M33_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _lang_tekst(kropp, felt: str, rid) -> str:
    """Ikke-tom OG lang nok.

    En metodebeskrivelse på tre tegn er ikke noe noen kan etterprøve,
    og en modell uten den er en autoritet uten begrunnelse.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_TEKST)
    if len(verdi) < MIN_TEKST:
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


def _minutter(kropp, felt: str, rid) -> int:
    """MINUTTER SOM HELTALL, aldri timer som flyttall.

    M-39s dom (113), arvet ordrett: `0.1 + 0.2` er ikke `0.3`, og en
    bemanningsmåling som samlet avrundingsfeil ville bommet med en tid
    ingen kan forklare. Resten av huset teller minutter; det gjør
    denne også.

    IKKE-NEGATIVT: en uke med negativt arbeid finnes ikke, og døra
    ville avvist den — men et nekt her gir en feilmelding kalleren kan
    handle på, i stedet for en `CheckViolation` oversatt i etterkant.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (0 <= verdi <= MAKS_MINUTTER):
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


def _dato_valgfri(kropp, felt: str, rid) -> str | None:
    if kropp.get(felt) is None:
        return None
    return _dato(kropp, felt, rid)


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(request.path_params[navn])
    except (KeyError, ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _kropp_uuid(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    Uten denne ville hvert lovlige nekt i 130 blitt en 500 — og en 500
    på «du kan ikke måle en uke som ikke er over» er en feilmelding
    ingen kan handle på (121-128s form).
    """
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, (psycopg.errors.RaiseException,
                      psycopg.errors.InvalidParameterValue,
                      psycopg.errors.InsufficientPrivilege,
                      psycopg.errors.CheckViolation,
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
        "prognoser": r[0], "modeller": r[1],
        "gyldige_modeller": r[2], "uker_totalt": r[3],
        "uker_maalt": r[4], "uker_umaalt": r[5],
        "treff": r[6], "bom": r[7], "apne_funn": r[8],
        "har_krav": r[9],
        # ALLE FIRE GRENSENE (123s lærdom): et skjema som viser mindre
        # enn det lagrer er en felle — flaten forhåndsutfyller herfra.
        "horisont_uker": r[10], "grunnlag_uker": r[11],
        "maalefrist_dogn": r[12], "domsgrunnlag_uker": r[13],
        "kravversjon": r[14],
        "prognoser_ukjent_kvalitet": r[15],
    }


def _prognoserad(r) -> dict:
    return {
        "prognose_id": str(r[0]), "laget_dato": r[1].isoformat(),
        "horisont_uker": r[2], "modellversjon": r[3],
        "baselinje": r[4], "grunnlag_uker": r[5],
        "grunnlag_siste_dato": r[6].isoformat(),
        "grunnlag_antall_uker": r[7],
        # TRE VERDIER, IKKE TO. `ren` og `ukjent` er ikke samme
        # tilstand: den ene betyr at M-3 har sett og ikke funnet noe,
        # den andre at ingen har sett etter.
        "datakvalitet": r[8], "datakvalitet_antall": r[9],
        "gjelder_til": r[10].isoformat(), "laget_av": r[11],
        "antall_maalt": r[12],
    }


def _banerad(r) -> dict:
    return {
        "uke_nr": r[0], "ukeslutt": r[1].isoformat(),
        # PUNKTET KOMMER ALDRI UTEN BÅNDET. `prognose_presentert_som
        # _faktum` håndheves der den faktisk kan brytes: i det som
        # forlater basen.
        "forventet_minutter": r[2], "nedre_minutter": r[3],
        "ovre_minutter": r[4], "baseline_minutter": r[5],
        "faktisk_minutter": r[6], "avvik_minutter": r[7],
        "baseline_avvik_minutter": r[8],
        "innenfor_intervall": r[9],
        # FLATEN REGNER IKKE UT SELV om uken er over — regelen bor i
        # basen og følger med hver rad (124s `kan_lukkes`-form).
        "kan_maales": r[10],
    }


def _modellrad(r) -> dict:
    return {
        "modell_id": str(r[0]), "navn": r[1], "versjon": r[2],
        "metode": r[3], "baselinje": r[4],
        "gyldig_fra": r[5].isoformat(),
        "gyldig_til": r[6].isoformat() if r[6] else None,
        "gjelder": r[7], "antall_prognoser": r[8],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1], "referanse": r[2],
        "detaljer": r[3], "over_grense": r[4], "apen": r[5],
        "forst_sett": r[6].isoformat(),
        "sist_sett": r[7].isoformat(), "lukket_av": r[8],
        # FLATEN SKAL IKKE HUSKE hvilke funn som er sveipens.
        "kan_lukkes": r[9],
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127/128s form).

    Flaten tegner sammendraget, prognosene, modellene og funnene i
    samme runde. Fire runder ville gitt fire mulige halvtegnede
    skjermer — og en flate der funnene og prognosene kunne komme fra
    ulike øyeblikk.
    """
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m33_bildet(%s)",
                         (tenant,)).fetchone()),
        "prognoser": [_prognoserad(r) for r in conn.execute(
            "SELECT * FROM m33_prognoseregister(%s,%s)",
            (tenant, MAKS_PROGNOSER)).fetchall()],
        "modeller": [_modellrad(r) for r in conn.execute(
            "SELECT * FROM m33_modellregister(%s)",
            (tenant,)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m33_prognosefunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def prognosebilde(tjeneste, request):
    """GET /v1/prognose (okonomi:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def prognoser_endepunkt(tjeneste, request):
    """GET /v1/prognose/prognoser (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute(
            "SELECT * FROM m33_prognoseregister(%s,%s)",
            (auth.tenant, MAKS_PROGNOSER)).fetchall()
        return kanonisk_json(
            {"prognoser": [_prognoserad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def bane_endepunkt(tjeneste, request):
    """GET /v1/prognose/{prognose_id}/bane (okonomi:read).

    HVER UKE KOMMER MED SITT BÅND. Det er ikke en pyntedetalj: et
    punktestimat uten spenn ER et tall som påstår å være et faktum, og
    det er nøyaktig det vaktsetningen forbyr.
    """
    from .lesing import _les, kanonisk_json
    from .app import _rid

    def _fn(conn, auth, rid):
        pid = _sti_uuid(request, "prognose_id", rid)
        rader = conn.execute("SELECT * FROM m33_banen(%s,%s)",
                             (auth.tenant, pid)).fetchall()
        return kanonisk_json(
            {"prognose_id": str(pid),
             "bane": [_banerad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    del _rid
    return _les(tjeneste, request, "okonomi:read", _fn)


def modeller_endepunkt(tjeneste, request):
    """GET /v1/prognose/modeller (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m33_modellregister(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"modeller": [_modellrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/prognose/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m33_prognosefunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen fem av de seks skriveveiene deler.

    `/prognose` bruker den IKKE: den returnerer en RAD med
    datakvalitetsflagget og basislinjen — den som ber om en prognose
    skal se hva den hviler på, ikke bare at den ble laget.
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
    """POST /v1/prognose/krav (bestilling:opprett, idem).

    `grunnlag_uker` ER TENANTENS BESLUTNING, og det er den viktigste
    av de fire: den er en PÅSTAND OM HVOR RASKT VIRKELIGHETEN ENDRER
    SEG. Et bemanningsmønster som svinger med sesong trenger et langt
    vindu; et selskap i vekst trenger et kort, fordi et langt snitt da
    alltid ligger under.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        h = _heltall(kropp, "horisont_uker", rid,
                     *KRAVGRENSER["horisont_uker"])
        g = _heltall(kropp, "grunnlag_uker", rid,
                     *KRAVGRENSER["grunnlag_uker"])
        m = _heltall(kropp, "maalefrist_dogn", rid,
                     *KRAVGRENSER["maalefrist_dogn"])
        d = _heltall(kropp, "domsgrunnlag_uker", rid,
                     *KRAVGRENSER["domsgrunnlag_uker"])
        return ("SELECT versjon FROM"
                " m33_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, h, g, m, d, bid, nokkel), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_modell_endepunkt(tjeneste, request):
    """POST /v1/prognose/modell (bestilling:opprett, idem).

    EN ALT AVVIKLET MODELLVERSJON KAN REGISTRERES: arkivet skal kunne
    svare på hvilken modell som gjaldt den gangen. Skillet går ved
    PROGNOSEN — `/prognose` nekter mot en versjon som ikke gjelder i
    dag (121s dom).

    `metode` OG `baselinje` ER OBLIGATORISKE, og det er klyngens dom:
    en modell uten en navngitt basislinje kan ikke måles mot noe, og
    `slaar_ikke_naiv_baseline` blir et funn ingen kan reise.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        versjon = _tekst(kropp, "versjon", rid, MAKS_NAVN)
        metode = _lang_tekst(kropp, "metode", rid)
        baselinje = _tekst(kropp, "baselinje", rid, MAKS_NAVN)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        mid = _utled("modell", tenant, nokkel)
        # SP-2: `modell_id` UTLEDES av Idempotency-Key-en, og døra
        # svarer med raden på et gjenspill i stedet for å treffe
        # primærnøkkelen. `ny` er ikke med i svaret her fordi
        # `_skriv` bærer ett felt — og «gjelder den i dag?» er det
        # kalleren faktisk trenger for å vite om den kan brukes.
        return ("SELECT gjelder FROM"
                " m33_registrer_modell(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, mid, navn, versjon, metode, baselinje, fra,
                 til, bid),
                {"modell_id": str(mid)}, "gjelder")
    return _skriv(tjeneste, request, bygg)


def avvikle_modell_endepunkt(tjeneste, request):
    """POST /v1/prognose/modell/{modell_id}/avvikle.

    ENESTE LOVLIGE ENDRING PÅ EN MODELL, og den er ENVEIS. En modell
    som kunne gjenoppvekkes ville gjort «hvilken modell gjaldt da?»
    ubesvarlig — og hver backtest til en sammenligning mot noe som kan
    ha endret seg etterpå.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        mid = _sti_uuid(request_, "modell_id", rid)
        til = _dato(kropp, "gyldig_til", rid)
        del nokkel
        return ("SELECT gyldig_til FROM"
                " m33_avvikle_modell(%s,%s,%s,%s)",
                (tenant, mid, til, bid),
                {"modell_id": str(mid)}, "gyldig_til")
    return _skriv(tjeneste, request, bygg)


def lag_prognose_endepunkt(tjeneste, request):
    """POST /v1/prognose/prognose (bestilling:opprett, idem).

    SVARET BÆRER HVA PROGNOSEN HVILER PÅ, ikke bare at den ble laget:
    datakvalitetsflagget, hvor mange uker historikk modellen faktisk
    fant, og basislinjen den skal måles mot. Et `{"ok": true}` ville
    krevd et nytt kall for å finne ut om prognosen ble regnet i blinde
    — og det kallet blir ikke alltid gjort.
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
        mid = _kropp_uuid(kropp, "modell_id", rid)
        pid = _utled("prognose", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m33_lag_prognose(%s,%s,%s,%s)",
                (tenant, pid, mid, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        # `ny` SKILLER ET GJENSPILL FRA EN NY PROGNOSE. Uten det
        # feltet ville to identiske svar betydd to ulike ting, og
        # kalleren måtte gjettet hvilket.
        return _ok({"prognose_id": str(pid), "horisont_uker": r[1],
                    "grunnlag_antall_uker": r[2],
                    "datakvalitet": r[3],
                    "baseline_minutter": r[4], "ny": r[5]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_maaling_endepunkt(tjeneste, request):
    """POST /v1/prognose/prognose/{prognose_id}/maaling.

    DEN ENESTE VEIEN TIL Å LUKKE `prognose_uten_maaling`.

    `innenfor_intervall` REGNES AV BÅNDET SOM STO PÅ RADEN, ikke av
    noe kalleren oppgir. Hadde kalleren fått si «ja, dette var
    innenfor», ville målingen vært en karakter modulen ga seg selv.

    Det samme gjelder `baseline_avvik_minutter`: basislinjen kopieres
    fra banen ved måling, så modelldommen sammenligner mot det som
    faktisk sto der da prognosen ble laget.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        pid = _sti_uuid(request_, "prognose_id", rid)
        uke = _heltall(kropp, "uke_nr", rid, 1, 52)
        faktisk = _minutter(kropp, "faktisk_minutter", rid)
        del nokkel
        return ("SELECT avvik_minutter FROM"
                " m33_registrer_maaling(%s,%s,%s,%s,%s)",
                (tenant, pid, uke, faktisk, bid),
                {"prognose_id": str(pid), "uke_nr": uke},
                "avvik_minutter")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/prognose/funn/{funn_id}/lukk.

    NEKTER PÅ SVEIPENS TO. `prognose_uten_maaling` lukkes av at
    målingen kommer; `slaar_ikke_naiv_baseline` av at modellen faktisk
    blir bedre. Kunne et menneske lukket dem, ville klyngens dom vært
    en anbefaling.

    `prognose_paa_ukjent_datakvalitet` KAN lukkes, og det er riktig:
    «vi vet at M-3 aldri har sett på dette, vi planlegger likevel» er
    en legitim beslutning med et navn på.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        fid = _sti_uuid(request_, "funn_id", rid)
        begrunnelse = _lang_tekst(kropp, "begrunnelse", rid)
        del nokkel
        return ("SELECT apen FROM m33_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, begrunnelse, bid),
                {"funn_id": str(fid)}, "apen")
    return _skriv(tjeneste, request, bygg)
