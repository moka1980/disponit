"""M-7 møteoperasjonsagentens API (migrasjon 133).

Fjorten endepunkter: seks leseveier og åtte skriveveier, alle mot
dører. Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_mote_eier`-eid SECURITY DEFINER-dør i 133, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM FATTER EN BESLUTNING.

`POST /beslutning` KREVER `besluttet_av`, og kolonnen er `NOT NULL` i
basen. En beslutning uten et menneske bak er ikke en beslutning modulen
skrev ned — det er en beslutning modulen FATTET, og det er nettopp det
den ikke gjør.

KLYNGENS DELTE DOM: EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS
TILBAKE — OG DEN SOM LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

FIRE NEKT SOM ER VERDT Å KJENNE, OG DE TRE FØRSTE ER SAMME DØR:

  * `POST /opptak` NEKTER uten hjemmel. Et opptak uten grunnlag er
    ulovlig i det øyeblikket det starter.

  * `POST /opptak` NEKTER på en UTLØPT hjemmel. En utløpt hjemmel ser
    nøyaktig ut som en gyldig — klynge 7s dom, og den gjelder her.

  * `POST /opptak` NEKTER hvis varslingen kom ETTER at opptaket
    startet, og hvis opptaket er datert fram i tid. ET NEKT SOM KOMMER
    ETTER MIKROFONEN ER IKKE ET NEKT: å oppdage et ulovlig opptak i en
    nattlig sveip er å oppdage en skade, ikke å hindre den.

  * `POST /referatpunkt` NEKTER uten registrerte grenser. Uten en
    sikkerhetsterskel kan ingenting merkes ubekreftet, og da er
    merkingen en tilfeldighet.

TERSKELEN OPPGIS ALDRI AV KALLEREN. Døra leser den fra tenantens krav
og skriver den på raden: en kaller som fikk sette sin egen terskel
kunne satt den til 1 og fått alt bekreftet.

SCOPENE. LESING `security:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `security:read` og IKKE `okonomi:read`, og skillet er
datasettet: et referat er hva navngitte mennesker sa i et møte, og et
opptak er en behandling av personopplysninger med hjemmel. Det er
compliance-leserens bord, ikke finansleserens — samme vurdering som
M-53 gjorde for HMS-avvik.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_MOTER = 200
MAKS_AKSJONER = 500
MAKS_FUNN = 200
MAKS_TEKST = 8000
MAKS_NAVN = 500
#: Beskrivelsen av en hjemmel må være SKREVET. Seksten tegn er ikke en
#: kvalitetsgaranti — det er en terskel mot «GDPR» som eneste
#: begrunnelse for et opptak noen skal svare for.
MIN_TEKST = 16
#: Maks antall deltakere eller varslede i ett kall. En liste over dette
#: er en importfeil, ikke et møte.
MAKS_LISTE = 500

GRUNNLAGSTYPER = ("samtykke", "avtale", "berettiget_interesse",
                  "rettslig_forpliktelse")
KILDER = ("opptak", "manuell", "agenda")
AKSJONSLUKKINGER = ("utfort", "henlagt")

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 133.
KRAVGRENSER = {
    "referatfrist_dogn": (1, 60),
    "aksjonsfrist_dogn": (1, 180),
    "sikkerhetsterskel_bp": (1, 10000),
}

_M7_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL,
                       "disponit:m07:moteoperasjon")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M7_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_TEKST)
    if len(verdi) < MIN_TEKST:
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


def _liste(kropp, felt: str, rid) -> list[str]:
    """EN LISTE MED TOMME STRENGER ER IKKE EN LISTE.

    `cardinality > 0` i basen fanger den TOMME lista. Denne fanger
    lista med tomme navn i — og den er den sannsynlige feilen, fordi
    et skjema som sender et ufylt felt sender `[""]`, ikke `[]`.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, list) or not verdi:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if len(verdi) > MAKS_LISTE:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for x in verdi:
        if not isinstance(x, str) or not x.strip():
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append(x.strip())
    return ut


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


def _tidspunkt(kropp, felt: str, rid) -> str:
    """ISO-tidspunkt MED sone, som streng.

    `fromisoformat` uten sone ville gitt en naiv tid, og en naiv tid i
    en `timestamptz`-kolonne tolkes i serverens sone — altså et annet
    tidspunkt enn kalleren mente. For et opptak er det forskjellen på
    «varslet før» og «varslet etter».
    """
    import datetime
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        t = datetime.datetime.fromisoformat(verdi)
    except ValueError as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e
    if t.tzinfo is None:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


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


def _kropp_uuid_valgfri(kropp, felt: str, rid):
    if kropp.get(felt) is None:
        return None
    return _kropp_uuid(kropp, felt, rid)


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    Uten denne ville hvert lovlige nekt i 133 blitt en 500 — og en 500
    på «varslingen kom etter at opptaket startet» er en feilmelding
    ingen kan handle på (121-132s form).
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
        "moter": r[0], "moter_uten_referat": r[1], "punkter": r[2],
        "ubekreftede": r[3], "beslutninger": r[4],
        # DET DYRESTE TALLET I MODULEN: en beslutning tatt på et punkt
        # maskinen var usikker på.
        "beslutninger_paa_ubekreftet": r[5],
        "apne_aksjoner": r[6], "aksjoner_over_frist": r[7],
        "opptak": r[8], "hjemler": r[9], "gyldige_hjemler": r[10],
        "apne_funn": r[11], "har_krav": r[12],
        # ALLE TRE GRENSENE (123s lærdom): et skjema som viser mindre
        # enn det lagrer er en felle.
        "referatfrist_dogn": r[13], "aksjonsfrist_dogn": r[14],
        "sikkerhetsterskel_bp": r[15], "kravversjon": r[16],
    }


def _moterad(r) -> dict:
    return {
        "mote_id": str(r[0]), "tittel": r[1],
        "start_ts": r[2].isoformat(), "slutt_ts": r[3].isoformat(),
        "innkalt_av": r[4], "antall_deltakere": r[5],
        "antall_punkter": r[6], "antall_ubekreftede": r[7],
        "antall_beslutninger": r[8], "antall_apne_aksjoner": r[9],
        "har_opptak": r[10],
        # DEN SOM SER AT ET MØTE BLE TATT OPP, SKAL SE HVORFOR DET VAR
        # LOV — uten et klikk til.
        "opptakshjemmel": r[11],
    }


def _punktrad(r) -> dict:
    return {
        "punkt_id": str(r[0]), "rekkefolge": r[1], "tekst": r[2],
        "kilde": r[3], "kilde_ref": r[4], "sikkerhet_bp": r[5],
        # TERSKELEN SOM GJALDT DA. Uten den kan «hvorfor er dette
        # merket?» ikke besvares etter at grensen er justert.
        "terskel_bp": r[6], "ubekreftet": r[7],
        "retter_punkt_id": str(r[8]) if r[8] else None,
        "registrert": r[9].isoformat(), "registrert_av": r[10],
        # ET RETTET PUNKT SKAL VÆRE SYNLIG SOM RETTET, ikke borte.
        "er_rettet": r[11],
    }


def _hjemmelrad(r) -> dict:
    return {
        "hjemmel_id": str(r[0]), "grunnlagstype": r[1],
        "beskrivelse": r[2], "formal": r[3],
        "gyldig_fra": r[4].isoformat(),
        "gyldig_til": r[5].isoformat() if r[5] else None,
        "gjelder": r[6], "antall_opptak": r[7],
    }


def _beslutningsrad(r) -> dict:
    return {
        "beslutning_id": str(r[0]), "tekst": r[1],
        "besluttet_av": r[2], "besluttet_ts": r[3].isoformat(),
        "punkt_id": str(r[4]) if r[4] else None,
        # USIKKERHETEN FORSVINNER IKKE fordi noen skrev «besluttet»
        # over den.
        "punkt_ubekreftet": r[5],
    }


def _aksjonsrad(r) -> dict:
    return {
        "aksjon_id": str(r[0]), "mote_id": str(r[1]), "tekst": r[2],
        "eier": r[3], "frist": r[4].isoformat(), "status": r[5],
        "lukket_av": r[6], "dogn_over_frist": r[7],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1], "referanse": r[2],
        "detaljer": r[3], "over_grense": r[4], "apen": r[5],
        "forst_sett": r[6].isoformat(), "sist_sett": r[7].isoformat(),
        "lukket_av": r[8],
        # FLATEN SKAL IKKE HUSKE hvilke funn som er sveipens.
        "kan_lukkes": r[9],
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127/128/130/132s form)."""
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m7_bildet(%s)",
                         (tenant,)).fetchone()),
        "moter": [_moterad(r) for r in conn.execute(
            "SELECT * FROM m7_moteregister(%s,%s)",
            (tenant, MAKS_MOTER)).fetchall()],
        "hjemler": [_hjemmelrad(r) for r in conn.execute(
            "SELECT * FROM m7_hjemmelregister(%s)",
            (tenant,)).fetchall()],
        "aksjoner": [_aksjonsrad(r) for r in conn.execute(
            "SELECT * FROM m7_aksjonene(%s,%s)",
            (tenant, MAKS_AKSJONER)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m7_motefunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def motebilde(tjeneste, request):
    """GET /v1/mote (security:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def moter_endepunkt(tjeneste, request):
    """GET /v1/mote/moter (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m7_moteregister(%s,%s)",
                             (auth.tenant, MAKS_MOTER)).fetchall()
        return kanonisk_json(
            {"moter": [_moterad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def referat_endepunkt(tjeneste, request):
    """GET /v1/mote/{mote_id}/referat (security:read).

    HVERT PUNKT KOMMER MED `ubekreftet` OG `terskel_bp`. Det er
    `usikkerhet_skjult` håndhevet der den faktisk kan brytes: i det som
    forlater basen. En flate kan velge å ikke merke punktet, men den
    kan ikke få et svar der merkingen mangler.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        mid = _sti_uuid(request, "mote_id", rid)
        punkter = conn.execute("SELECT * FROM m7_referatet(%s,%s)",
                               (auth.tenant, mid)).fetchall()
        beslutninger = conn.execute(
            "SELECT * FROM m7_beslutningene(%s,%s)",
            (auth.tenant, mid)).fetchall()
        return kanonisk_json(
            {"mote_id": str(mid),
             "punkter": [_punktrad(r) for r in punkter],
             "beslutninger": [_beslutningsrad(r)
                              for r in beslutninger],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def hjemler_endepunkt(tjeneste, request):
    """GET /v1/mote/hjemler (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m7_hjemmelregister(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"hjemler": [_hjemmelrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def aksjoner_endepunkt(tjeneste, request):
    """GET /v1/mote/aksjoner (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m7_aksjonene(%s,%s)",
                             (auth.tenant, MAKS_AKSJONER)).fetchall()
        return kanonisk_json(
            {"aksjoner": [_aksjonsrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/mote/funn (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m7_motefunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen sju av de åtte skriveveiene deler.

    `/opptak` bruker den IKKE: den returnerer GRUNNLAGSTYPEN som gjorde
    opptaket lovlig. Den som starter et opptak skal se hva det hviler
    på i samme svar — ikke måtte slå det opp etterpå.
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
    """POST /v1/mote/krav (bestilling:opprett, idem).

    `sikkerhetsterskel_bp` ER TENANTENS BESLUTNING. Vaktsetningen sier
    «lav sikkerhet merkes som ubekreftet», men HVA som er lavt er en
    vurdering av hvor mye det koster å ta feil — og et styremøte og en
    ukentlig statusrunde tåler ikke det samme.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        r = _heltall(kropp, "referatfrist_dogn", rid,
                     *KRAVGRENSER["referatfrist_dogn"])
        a = _heltall(kropp, "aksjonsfrist_dogn", rid,
                     *KRAVGRENSER["aksjonsfrist_dogn"])
        t = _heltall(kropp, "sikkerhetsterskel_bp", rid,
                     *KRAVGRENSER["sikkerhetsterskel_bp"])
        return ("SELECT versjon FROM m7_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, r, a, t, bid, nokkel), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_hjemmel_endepunkt(tjeneste, request):
    """POST /v1/mote/hjemmel (bestilling:opprett, idem).

    `grunnlagstype` ER ET LUKKET SETT PÅ FIRE, ikke en boolsk «samtykke
    ja/nei». Samtykke er ETT av grunnlagene, og i en arbeidsrelasjon
    ofte det svakeste — maktubalansen gjør det. En modell som bare
    kjente samtykke ville tvunget fram et ugyldig grunnlag for å komme
    videre.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        grunnlagstype = _valg(kropp, "grunnlagstype", rid,
                              GRUNNLAGSTYPER)
        beskrivelse = _lang_tekst(kropp, "beskrivelse", rid)
        formal = _tekst(kropp, "formal", rid, MAKS_NAVN)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        hid = _utled("hjemmel", tenant, nokkel)
        return ("SELECT gjelder FROM"
                " m7_registrer_hjemmel(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, hid, grunnlagstype, beskrivelse, formal, fra,
                 til, bid),
                {"hjemmel_id": str(hid)}, "gjelder")
    return _skriv(tjeneste, request, bygg)


def avslutt_hjemmel_endepunkt(tjeneste, request):
    """POST /v1/mote/hjemmel/{hjemmel_id}/avslutt.

    ENVEIS. En hjemmel som kunne gjenoppvekkes ville gjort «hva hvilte
    opptaket på?» til et oppslag i noe som har endret seg siden.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        hid = _sti_uuid(request_, "hjemmel_id", rid)
        til = _dato(kropp, "gyldig_til", rid)
        del nokkel
        return ("SELECT gyldig_til FROM"
                " m7_avslutt_hjemmel(%s,%s,%s,%s)",
                (tenant, hid, til, bid),
                {"hjemmel_id": str(hid)}, "gyldig_til")
    return _skriv(tjeneste, request, bygg)


def registrer_mote_endepunkt(tjeneste, request):
    """POST /v1/mote/mote (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        tittel = _tekst(kropp, "tittel", rid, MAKS_NAVN)
        start = _tidspunkt(kropp, "start_ts", rid)
        slutt = _tidspunkt(kropp, "slutt_ts", rid)
        innkalt_av = _tekst(kropp, "innkalt_av", rid, MAKS_NAVN)
        deltakere = _liste(kropp, "deltakere", rid)
        agenda = _tekst(kropp, "agenda", rid, MAKS_TEKST)
        mid = _utled("mote", tenant, nokkel)
        return ("SELECT ny FROM"
                " m7_registrer_mote(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, mid, tittel, start, slutt, innkalt_av,
                 deltakere, agenda, bid),
                {"mote_id": str(mid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def start_opptak_endepunkt(tjeneste, request):
    """POST /v1/mote/{mote_id}/opptak (bestilling:opprett, idem).

    MODULENS VIKTIGSTE RUTE, OG DEN ENESTE HANDLINGEN SOM IKKE KAN
    GJØRES UGJORT.

    Døra nekter på fire ting FØR raden finnes: hjemmelen mangler,
    hjemmelen er utløpt, ingen er varslet, eller varslingen kom etter
    at opptaket startet. ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE
    ET NEKT.

    SVARET BÆRER GRUNNLAGSTYPEN. Den som starter et opptak skal se hva
    det hviler på i samme svar.
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
        mid = _sti_uuid(request, "mote_id", rid)
        hid = _kropp_uuid(kropp, "hjemmel_id", rid)
        varslet_ts = _tidspunkt(kropp, "varslet_ts", rid)
        varslet_av = _tekst(kropp, "varslet_av", rid, MAKS_NAVN)
        varslede = _liste(kropp, "varslede", rid)
        startet = _tidspunkt(kropp, "startet_ts", rid)
        oid = _utled("opptak", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM"
                " m7_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, oid, mid, hid, varslet_ts, varslet_av,
                 varslede, startet, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"opptak_id": str(oid), "grunnlagstype": r[1],
                    "ny": r[2]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_referatpunkt_endepunkt(tjeneste, request):
    """POST /v1/mote/{mote_id}/referatpunkt.

    `sikkerhetsterskel` OPPGIS IKKE AV KALLEREN. Døra leser den fra
    tenantens krav og skriver den på raden: en kaller som fikk sette
    sin egen terskel kunne satt den til 1 og fått alt bekreftet.

    `ubekreftet` regnes av døra av samme grunn, og CHECKen i tabellen
    fanger det uansett — to lag, fordi et referat som skjuler
    usikkerhet er en påstand om at maskinen var sikker.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        mid = _sti_uuid(request_, "mote_id", rid)
        rekkefolge = _heltall(kropp, "rekkefolge", rid, 1, 100000)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        kilde = _valg(kropp, "kilde", rid, KILDER)
        kilde_ref = _tekst(kropp, "kilde_ref", rid, MAKS_NAVN)
        sikkerhet = _heltall(kropp, "sikkerhet_bp", rid, 0, 10000)
        retter = _kropp_uuid_valgfri(kropp, "retter_punkt_id", rid)
        pid = _utled("punkt", tenant, nokkel)
        return ("SELECT ubekreftet FROM"
                " m7_registrer_referatpunkt"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, pid, mid, rekkefolge, tekst, kilde,
                 kilde_ref, sikkerhet, retter, bid),
                {"punkt_id": str(pid)}, "ubekreftet")
    return _skriv(tjeneste, request, bygg)


def registrer_beslutning_endepunkt(tjeneste, request):
    """POST /v1/mote/{mote_id}/beslutning.

    `besluttet_av` ER OBLIGATORISK, OG DET ER HELE V1-DOMMEN. En
    beslutning uten et menneske bak er ikke en beslutning modulen skrev
    ned — det er en beslutning modulen FATTET.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        mid = _sti_uuid(request_, "mote_id", rid)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        besluttet_av = _tekst(kropp, "besluttet_av", rid, MAKS_NAVN)
        besluttet_ts = _tidspunkt(kropp, "besluttet_ts", rid)
        punkt = _kropp_uuid_valgfri(kropp, "punkt_id", rid)
        beid = _utled("beslutning", tenant, nokkel)
        return ("SELECT ny FROM"
                " m7_registrer_beslutning(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, beid, mid, tekst, besluttet_av, besluttet_ts,
                 punkt, bid),
                {"beslutning_id": str(beid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def registrer_aksjon_endepunkt(tjeneste, request):
    """POST /v1/mote/{mote_id}/aksjon.

    `eier` ER OBLIGATORISK. En aksjon uten eier er en aksjon ingen
    gjør.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        mid = _sti_uuid(request_, "mote_id", rid)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        eier = _tekst(kropp, "eier", rid, MAKS_NAVN)
        frist = _dato(kropp, "frist", rid)
        aid = _utled("aksjon", tenant, nokkel)
        return ("SELECT ny FROM"
                " m7_registrer_aksjon(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, aid, mid, tekst, eier, frist, bid),
                {"aksjon_id": str(aid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def lukk_aksjon_endepunkt(tjeneste, request):
    """POST /v1/mote/aksjon/{aksjon_id}/lukk."""
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        aid = _sti_uuid(request_, "aksjon_id", rid)
        status = _valg(kropp, "status", rid, AKSJONSLUKKINGER)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_TEKST)
        del nokkel
        return ("SELECT status FROM"
                " m7_lukk_aksjon(%s,%s,%s,%s,%s)",
                (tenant, aid, status, begrunnelse, bid),
                {"aksjon_id": str(aid)}, "status")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/mote/funn/{funn_id}/lukk.

    NEKTER PÅ SVEIPENS TO. `mote_uten_referat` lukkes av at referatet
    skrives; `aksjon_over_frist` av at aksjonen lukkes.

    `ubekreftet_punkt_uavklart` KAN lukkes: «vi har lest det, det
    stemmer» er en legitim avklaring med et navn på.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        fid = _sti_uuid(request_, "funn_id", rid)
        begrunnelse = _lang_tekst(kropp, "begrunnelse", rid)
        del nokkel
        return ("SELECT apen FROM m7_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, begrunnelse, bid),
                {"funn_id": str(fid)}, "apen")
    return _skriv(tjeneste, request, bygg)
