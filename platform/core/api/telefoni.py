"""M-43 tale- og telefoniagentens API (migrasjon 135).

Sytten endepunkter: seks leseveier og elleve skriveveier, alle mot
dører. Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_telefoni_eier`-eid SECURITY DEFINER-dør i 135, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM INNGÅR EN AVTALE ELLER LOVER PENGER.

Vaktsetningen krever «eksplisitt policy» for begge, og v1 har ingen vei
dit i det hele tatt — ikke en avslått vei, ikke en vei bak en bryter.
Det finnes ingen kropp med et beløp i, og ingen rute som binder noe.

KLYNGENS DELTE DOM: EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS
TILBAKE — OG DEN SOM LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

HER ER DEN BOKSTAVELIG. Den andre parten HØRER en stemme, og en stemme
høres ikke ut som en maskin lenger. Den som tror hun snakker med et
menneske, svarer annerledes.

TRE NEKT PÅ `POST /samtale`, ALLE FØR RADEN FINNES:

  * Identifikasjonen er datert FØR samtalen startet. En identifikasjon
    ingen kunne hørt er ingen identifikasjon.
  * Identifikasjonen kom for SENT etter tenantens egen frist.
  * Tenanten har ingen grenser, og da kan «for sent» ikke måles.

…OG `POST /linje` NEKTER en linje datert før identifikasjonen.
INGENTING BLE SAGT FØR VI SA HVA VI ER.

FIRE NEKT PÅ `POST /opptak`, ARVET ORDRETT FRA 133: manglende hjemmel,
utløpt hjemmel, ingen varslet, og varsling som kom etter at opptaket
startet. ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE ET NEKT.

HJEMMELEN ER M-7s, OG DEN ARVES. `POST /hjemmel` skriver i
`opptakshjemmel` (133). To modeller for samme hjemmel ville gitt to
svar på «hadde vi lov».

TERSKELEN OPPGIS ALDRI AV KALLEREN. Døra leser den fra tenantens krav.

SCOPENE. LESING `security:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `security:read`: en transkripsjon er hva navngitte
mennesker sa i en samtale, og et opptak er en behandling av
personopplysninger med hjemmel. Samme vurdering som M-53 for HMS-avvik,
M-7 for referater og M-20 for produktpåstander.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_SAMTALER = 200
MAKS_ESKALERINGER = 500
MAKS_FUNN = 200
MAKS_TEKST = 8000
MAKS_NAVN = 500
#: En hjemmelsbeskrivelse må være SKREVET. Seksten tegn er ikke en
#: kvalitetsgaranti — det er en terskel mot «GDPR» som eneste
#: begrunnelse for et opptak noen skal svare for. 133s tall.
MIN_TEKST = 16
#: Maks antall varslede i ett kall. En liste over dette er en
#: importfeil, ikke en samtale.
MAKS_LISTE = 500
#: Maks linjenummer. En samtale med flere linjer enn dette er en
#: transkripsjonsfeil.
MAKS_LINJER = 20000

RETNINGER = ("inngaaende", "utgaaende")
TALERE = ("agent", "motpart", "menneske")
LINJEKILDER = ("transkripsjon", "manuell")
GRUNNLAGSTYPER = ("samtykke", "avtale", "berettiget_interesse",
                  "rettslig_forpliktelse")
ESKALERINGSUTFALL = ("haandtert", "henlagt")

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 135.
KRAVGRENSER = {
    "sikkerhetsterskel_bp": (1, 10000),
    "identifikasjonsfrist_sek": (1, 120),
    "eskaleringsfrist_dogn": (1, 90),
    "samtaletak_timer": (1, 168),
}

_M43_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m43:telefoni")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M43_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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
    lista med tomme navn i — og den er den sannsynlige feilen, fordi et
    skjema som sender et ufylt felt sender `[""]`, ikke `[]`.
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


def _dato_valgfri(kropp, felt: str, rid):
    if kropp.get(felt) is None:
        return None
    return _dato(kropp, felt, rid)


def _tidspunkt(kropp, felt: str, rid) -> str:
    """ISO-tidspunkt MED sone, som streng.

    `fromisoformat` uten sone ville gitt en naiv tid, og en naiv tid i
    en `timestamptz` tolkes i serverens sone — altså et annet tidspunkt
    enn kalleren mente. For en identifikasjon er det forskjellen på
    «sagt i tide» og «sagt for sent».
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

    Uten denne ville hvert lovlige nekt i 135 blitt en 500 — og en 500
    på «agenten sa hva den er etter 45 sekunder» er en feilmelding
    ingen kan handle på (121-134s form).

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
# RADBYGGERNE — ÉN FORM, ETT STED.
# ---------------------------------------------------------------------

def _bilderad(r) -> dict:
    return {
        "samtaler": r[0], "apne_samtaler": r[1], "linjer": r[2],
        "ubekreftede": r[3], "opptak": r[4], "hjemler": r[5],
        "gyldige_hjemler": r[6], "regler": r[7],
        "gjeldende_regler": r[8], "eskaleringer": r[9],
        "apne_eskaleringer": r[10], "apne_funn": r[11],
        # DET DYRESTE TALLET I MODULEN: den lengste tiden noen snakket
        # med en maskin uten å vite det.
        "tregeste_identifikasjon_sek": r[12],
        "har_krav": r[13],
        # ALLE FIRE GRENSENE (123s lærdom): et skjema som viser mindre
        # enn det lagrer er en felle.
        "sikkerhetsterskel_bp": r[14],
        "identifikasjonsfrist_sek": r[15],
        "eskaleringsfrist_dogn": r[16], "samtaletak_timer": r[17],
        "kravversjon": r[18],
    }


def _samtalerad(r) -> dict:
    return {
        "samtale_id": str(r[0]), "retning": r[1], "motpart": r[2],
        "startet_ts": r[3].isoformat(),
        "slutt_ts": r[4].isoformat() if r[4] else None,
        # HVOR LENGE DEN ANDRE PARTEN SNAKKET UTEN Å VITE HVA HUN
        # SNAKKET MED.
        "sekunder_til_identifikasjon": r[5],
        # …OG HVA AGENTEN FAKTISK SA. «Agenten identifiserte seg» er en
        # påstand; dette er ordlyden.
        "identifikasjonstekst": r[6],
        "antall_linjer": r[7], "antall_ubekreftede": r[8],
        "antall_apne_eskaleringer": r[9], "har_opptak": r[10],
        # DEN SOM SER AT EN SAMTALE BLE TATT OPP, SKAL SE HVORFOR DET
        # VAR LOV — uten et klikk til.
        "opptakshjemmel": r[11],
    }


def _linjerad(r) -> dict:
    return {
        "linje_id": str(r[0]), "rekkefolge": r[1], "taler": r[2],
        "linje_ts": r[3].isoformat(), "tekst": r[4], "kilde": r[5],
        "sikkerhet_bp": r[6],
        # TERSKELEN SOM GJALDT DA. Uten den kan «hvorfor er dette
        # merket?» ikke besvares etter at grensen er justert.
        "terskel_bp": r[7], "ubekreftet": r[8],
        "retter_linje_id": str(r[9]) if r[9] else None,
        # EN RETTET LINJE ER SYNLIG SOM RETTET, ikke borte.
        "er_rettet": r[10],
        "registrert": r[11].isoformat(), "registrert_av": r[12],
    }


def _regelrad(r) -> dict:
    return {
        "regel_id": str(r[0]), "beskrivelse": r[1], "mottaker": r[2],
        "gyldig_fra": r[3].isoformat(),
        "gyldig_til": r[4].isoformat() if r[4] else None,
        "gjelder": r[5], "antall_eskaleringer": r[6],
    }


def _hjemmelrad(r) -> dict:
    return {
        "hjemmel_id": str(r[0]), "grunnlagstype": r[1],
        "beskrivelse": r[2], "formal": r[3],
        "gyldig_fra": r[4].isoformat(),
        "gyldig_til": r[5].isoformat() if r[5] else None,
        "gjelder": r[6], "antall_opptak": r[7],
    }


def _eskaleringsrad(r) -> dict:
    return {
        "eskalering_id": str(r[0]), "samtale_id": str(r[1]),
        "regel_id": str(r[2]),
        # REGELEN SOM BAR DEN, i samme rad. En eskalering uten en regel
        # å peke på er modulens egen beslutning.
        "regeltekst": r[3], "mottaker": r[4], "begrunnelse": r[5],
        "eskalert_ts": r[6].isoformat(), "eskalert_av": r[7],
        "lukket_ts": r[8].isoformat() if r[8] else None,
        "lukket_av": r[9], "lukket_utfall": r[10], "dogn_apen": r[11],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1],
        "referanse": str(r[2]), "detaljer": r[3],
        "over_grense": r[4], "apen": r[5],
        "forst_sett": r[6].isoformat(), "sist_sett": r[7].isoformat(),
        "lukket_av": r[8],
        # HVEM SOM KAN LUKKE HVA, LEST FRA BASEN (132s CodeRabbit-funn).
        "kan_lukkes": r[9],
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127/128/130/132/133/134s form)."""
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m43_bildet(%s)",
                         (tenant,)).fetchone()),
        "samtaler": [_samtalerad(r) for r in conn.execute(
            "SELECT * FROM m43_samtaleregister(%s,%s)",
            (tenant, MAKS_SAMTALER)).fetchall()],
        "hjemler": [_hjemmelrad(r) for r in conn.execute(
            "SELECT * FROM m43_hjemlene(%s)", (tenant,)).fetchall()],
        "regler": [_regelrad(r) for r in conn.execute(
            "SELECT * FROM m43_reglene(%s)", (tenant,)).fetchall()],
        "eskaleringer": [_eskaleringsrad(r) for r in conn.execute(
            "SELECT * FROM m43_eskaleringene(%s,%s)",
            (tenant, MAKS_ESKALERINGER)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m43_telefonifunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def telefonibilde(tjeneste, request):
    """GET /v1/telefoni (security:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def samtaler_endepunkt(tjeneste, request):
    """GET /v1/telefoni/samtaler (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m43_samtaleregister(%s,%s)",
                             (auth.tenant, MAKS_SAMTALER)).fetchall()
        return kanonisk_json(
            {"samtaler": [_samtalerad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def transkripsjon_endepunkt(tjeneste, request):
    """GET /v1/telefoni/samtale/{samtale_id}/transkripsjon.

    LINJENE MED SIN USIKKERHET, og terskelen som gjaldt DA.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        sid = _sti_uuid(request, "samtale_id", rid)
        rader = conn.execute("SELECT * FROM m43_transkripsjonen(%s,%s)",
                             (auth.tenant, sid)).fetchall()
        return kanonisk_json(
            {"samtale_id": str(sid),
             "linjer": [_linjerad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def hjemler_endepunkt(tjeneste, request):
    """GET /v1/telefoni/hjemler (security:read) — den DELTE hjemmelen."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m43_hjemlene(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"hjemler": [_hjemmelrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def regler_endepunkt(tjeneste, request):
    """GET /v1/telefoni/regler (security:read) — kundens regler."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m43_reglene(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"regler": [_regelrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/telefoni/funn (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m43_telefonifunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen åtte av de elleve skriveveiene deler.

    TRE STÅR UTENFOR, og hver av dem fordi svaret er mer enn en
    kvittering:

      * `/samtale` returnerer SEKUNDENE TIL IDENTIFIKASJON. Den som
        starter en samtale skal se hvor lenge den andre parten snakket
        uten å vite hva hun snakket med — i samme svar.
      * `/opptak` returnerer GRUNNLAGSTYPEN som gjorde opptaket lovlig
        (133s form).
      * `/linje` returnerer TERSKELEN og om linjen ble merket. Kalleren
        oppgir aldri terskelen, så den må komme tilbake.
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
    """POST /v1/telefoni/krav (bestilling:opprett, idem).

    ALLE FIRE GRENSENE ER TENANTENS. Hvor sikker en transkripsjon må
    være, og hvor raskt agenten må si hva den er, er vurderinger av
    hvor mye det koster å ta feil — og en bestilling av pizza og en
    samtale om oppsigelse tåler ikke det samme.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        t = _heltall(kropp, "sikkerhetsterskel_bp", rid,
                     *KRAVGRENSER["sikkerhetsterskel_bp"])
        i = _heltall(kropp, "identifikasjonsfrist_sek", rid,
                     *KRAVGRENSER["identifikasjonsfrist_sek"])
        e = _heltall(kropp, "eskaleringsfrist_dogn", rid,
                     *KRAVGRENSER["eskaleringsfrist_dogn"])
        s = _heltall(kropp, "samtaletak_timer", rid,
                     *KRAVGRENSER["samtaletak_timer"])
        del nokkel
        return ("SELECT m43_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, t, i, e, s, bid), {}, "kravversjon")
    return _skriv(tjeneste, request, bygg)


def registrer_hjemmel_endepunkt(tjeneste, request):
    """POST /v1/telefoni/hjemmel (bestilling:opprett, idem).

    SKRIVER I DEN DELTE HJEMMELEN (133), ikke i en egen. To modeller
    for samme hjemmel ville gitt to svar på «hadde vi lov».

    `grunnlagstype` ER ET LUKKET SETT PÅ FIRE, ikke en boolsk «samtykke
    ja/nei». Samtykke er ETT av grunnlagene, og i en kunderelasjon er
    det ofte ikke det riktige.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        typ = _valg(kropp, "grunnlagstype", rid, GRUNNLAGSTYPER)
        besk = _lang_tekst(kropp, "beskrivelse", rid)
        formal = _tekst(kropp, "formal", rid, MAKS_NAVN)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        hid = _utled("hjemmel", tenant, nokkel)
        return ("SELECT m43_registrer_hjemmel(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, hid, typ, besk, formal, fra, til, bid),
                {}, "hjemmel_id")
    return _skriv(tjeneste, request, bygg)


def registrer_regel_endepunkt(tjeneste, request):
    """POST /v1/telefoni/regel (bestilling:opprett, idem).

    «ESKALERINGSREGLER ER KUNDENS.» `mottaker` er påkrevd: en
    eskalering uten en mottaker er en alarm i et tomt rom.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        besk = _lang_tekst(kropp, "beskrivelse", rid)
        mottaker = _tekst(kropp, "mottaker", rid, MAKS_NAVN)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        rid_ = _utled("regel", tenant, nokkel)
        return ("SELECT m43_registrer_regel(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, rid_, besk, mottaker, fra, til, bid),
                {}, "regel_id")
    return _skriv(tjeneste, request, bygg)


def avvikle_regel_endepunkt(tjeneste, request):
    """POST /v1/telefoni/regel/{regel_id}/avvikle.

    ENVEIS. En regel som kunne gjenopplives ville gjort «hvilken regel
    gjaldt da» til et spørsmål med to svar.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        r = _sti_uuid(request_, "regel_id", rid)
        til = _dato(kropp, "gyldig_til", rid)
        return ("SELECT m43_avvikle_regel(%s,%s,%s,%s)",
                (tenant, r, til, bid), {"regel_id": str(r)}, "avviklet")
    return _skriv(tjeneste, request, bygg)


def start_samtale_endepunkt(tjeneste, request):
    """POST /v1/telefoni/samtale (bestilling:opprett, idem).

    MODULENS VIKTIGSTE RUTE.

    `identifisert_ts` OG `identifikasjonstekst` ER BEGGE PÅKREVD.
    Tidspunktet måles mot tenantens frist; teksten er hva agenten
    faktisk sa. «Agenten identifiserte seg» er en påstand — teksten er
    en måling.

    SVARET BÆRER SEKUNDENE. Den som starter en samtale skal se hvor
    lenge den andre parten snakket uten å vite hva hun snakket med, i
    samme svar — ikke måtte regne det ut etterpå.
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
        retning = _valg(kropp, "retning", rid, RETNINGER)
        motpart = _tekst(kropp, "motpart", rid, MAKS_NAVN)
        start = _tidspunkt(kropp, "startet_ts", rid)
        ident = _tidspunkt(kropp, "identifisert_ts", rid)
        tekst = _tekst(kropp, "identifikasjonstekst", rid, MAKS_TEKST)
        sid = _utled("samtale", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM"
                " m43_start_samtale(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, sid, retning, motpart, start, ident, tekst,
                 bid)).fetchone()
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
        return _ok({"samtale_id": str(r[0]),
                    "sekunder_til_identifikasjon": r[1]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def avslutt_samtale_endepunkt(tjeneste, request):
    """POST /v1/telefoni/samtale/{samtale_id}/avslutt."""
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        sid = _sti_uuid(request_, "samtale_id", rid)
        slutt = _tidspunkt(kropp, "slutt_ts", rid)
        return ("SELECT m43_avslutt_samtale(%s,%s,%s,%s)",
                (tenant, sid, slutt, bid),
                {"samtale_id": str(sid)}, "avsluttet")
    return _skriv(tjeneste, request, bygg)


def start_opptak_endepunkt(tjeneste, request):
    """POST /v1/telefoni/samtale/{samtale_id}/opptak.

    DEN ENESTE HANDLINGEN I MODULEN SOM IKKE KAN GJØRES UGJORT.

    Døra nekter på fire ting FØR raden finnes: hjemmelen mangler,
    hjemmelen er utløpt, ingen er varslet, eller varslingen kom etter
    at opptaket startet. ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE
    ET NEKT.

    GYLDIGHETEN MÅLES MED M-7s EGEN FUNKSJON, ikke med en kopi.

    SVARET BÆRER GRUNNLAGSTYPEN (133s form).
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
        sid = _sti_uuid(request, "samtale_id", rid)
        hid = _kropp_uuid(kropp, "hjemmel_id", rid)
        varslet_ts = _tidspunkt(kropp, "varslet_ts", rid)
        varslet_av = _tekst(kropp, "varslet_av", rid, MAKS_NAVN)
        varslede = _liste(kropp, "varslede", rid)
        startet = _tidspunkt(kropp, "startet_ts", rid)
        oid = _utled("opptak", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM"
                " m43_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, oid, sid, hid, varslet_ts, varslet_av,
                 varslede, startet, bid)).fetchone()
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
        return _ok({"opptak_id": str(r[0]), "grunnlagstype": r[1],
                    "ny": r[2]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_linje_endepunkt(tjeneste, request):
    """POST /v1/telefoni/samtale/{samtale_id}/linje.

    INGENTING BLE SAGT FØR VI SA HVA VI ER. Døra nekter en linje
    datert FØR `identifisert_ts`.

    TERSKELEN OPPGIS ALDRI AV KALLEREN — og `sikkerhet_bp` gjør det,
    fordi den kommer fra transkripsjonsmotoren. En kaller som fikk
    sette terskelen kunne satt den til 1 og fått alt bekreftet; en
    kaller som setter sikkerheten forteller bare hva motoren mente.

    SVARET BÆRER TERSKELEN OG MERKINGEN, så kalleren ser om linjen ble
    flagget uten å slå det opp.
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
        sid = _sti_uuid(request, "samtale_id", rid)
        rekkefolge = _heltall(kropp, "rekkefolge", rid, 1, MAKS_LINJER)
        taler = _valg(kropp, "taler", rid, TALERE)
        linje_ts = _tidspunkt(kropp, "linje_ts", rid)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        kilde = _valg(kropp, "kilde", rid, LINJEKILDER)
        sikkerhet = _heltall(kropp, "sikkerhet_bp", rid, 0, 10000)
        retter = _kropp_uuid_valgfri(kropp, "retter_linje_id", rid)
        lid = _utled("linje", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m43_registrer_linje"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, lid, sid, rekkefolge, taler, linje_ts, tekst,
                 kilde, sikkerhet, retter, bid)).fetchone()
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
        return _ok({"linje_id": str(r[0]), "terskel_bp": r[1],
                    "ubekreftet": r[2]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def eskaler_endepunkt(tjeneste, request):
    """POST /v1/telefoni/samtale/{samtale_id}/eskaler.

    `regel_id` ER PÅKREVD, og det er hele invarianten. En eskalering
    uten en regel er ikke validert bort — den er urepresenterbar:
    kolonnen er NOT NULL med fremmednøkkel til tenantens egen regel.

    SVARET BÆRER MOTTAKEREN som regelen navnga, så kalleren ser hvem
    som ble vekket.
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
        sid = _sti_uuid(request, "samtale_id", rid)
        regel = _kropp_uuid(kropp, "regel_id", rid)
        grunn = _tekst(kropp, "begrunnelse", rid, MAKS_TEKST)
        eid = _utled("eskalering", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m43_eskaler(%s,%s,%s,%s,%s,%s)",
                (tenant, eid, sid, regel, grunn, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"eskalering_id": str(r[0]), "mottaker": r[1]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def lukk_eskalering_endepunkt(tjeneste, request):
    """POST /v1/telefoni/eskalering/{eskalering_id}/lukk.

    `utfall` ER ET LUKKET SETT PÅ TO: `haandtert` eller `henlagt`. En
    lukking uten et utfall ville gjort «ble det gjort noe» til et
    spørsmål ingen kan svare på etterpå.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        eid = _sti_uuid(request_, "eskalering_id", rid)
        utfall = _valg(kropp, "utfall", rid, ESKALERINGSUTFALL)
        return ("SELECT m43_lukk_eskalering(%s,%s,%s,%s)",
                (tenant, eid, utfall, bid),
                {"eskalering_id": str(eid)}, "lukket")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/telefoni/funn/{funn_id}/lukk.

    DØRA NEKTER PÅ SVEIPENS EGNE. `samtale_uten_avslutning` og
    `eskalering_over_frist` lukkes av at TILSTANDEN opphører — ikke av
    at noen huker av. En eskalering ingen tok blir ikke tatt av at noen
    leste varselet.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        fid = _sti_uuid(request_, "funn_id", rid)
        grunn = _tekst(kropp, "grunn", rid, MAKS_TEKST)
        return ("SELECT m43_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, grunn, bid),
                {"funn_id": str(fid)}, "lukket")
    return _skriv(tjeneste, request, bygg)
