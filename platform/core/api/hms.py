"""M-53 HMS- og avviksmottakets API (migrasjon 127).

Tolv endepunkter: seks leseveier og seks skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_hms_eier`-eid SECURITY DEFINER-dør i 127, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM VARSLER EN MYNDIGHET, og ingen som lukker et
avvik uten et tiltak å vise til.

DEN VIKTIGSTE LINJEN I HELE FILEN STÅR I `_meld`:

    aktor = None if melderform == "anonym" else bid

`_browserkontekst` gir tenanten og BRUKER-ID-en. For et anonymt avvik
sendes bruker-id-en ALDRI videre. Ikke satt til tom streng, ikke
maskert — ikke sendt. `revisjonslogg` er append-only siden 001, og et
navn som lekker inn der kan aldri fjernes igjen: den samme garantien
som gjør beviskjeden troverdig, gjør lekkasjen permanent.

PRISEN, SKREVET NED: plattformen kan heller ikke spore et anonymt
avvik. Ikke «vil ikke» — KAN IKKE. En anonym kanal som ikke kan spores
kan også misbrukes, og det aksepteres, fordi alternativet er et
varslervern vi holder helt til noen med nok myndighet spør.

FEM NEKT SOM ER VERDT Å KJENNE:

  * `POST /avvik` NEKTER et anonymt avvik som bærer et meldernavn.
    Anonymitet er en TILSTAND, ikke et tomt felt — og en dør som
    stille kastet navnet ville sett riktig ut i hver test.

  * `POST /avvik` NEKTER uten tenantens oppbevaringsgrenser, og uten
    et gjeldende regelverk for avvikstypen. Et avvik uten
    oppbevaringshjemmel skal ikke kunne oppstå.

  * `POST /avvik` NEKTER en hjemmel som krever lengre oppbevaring enn
    tenantens tak.

  * `POST /avvik/{id}/anonymiser` NEKTER før oppbevaringsfristen med
    mindre en M-30-sak oppgis. Arbeidstilsynet krever at avviket
    bevares; en tidlig sletting uten hjemmel er et bortkommet bevis.

  * `POST /funn/{id}/lukk` NEKTER på tre funntyper. Regelen bor i
    basen (`m53_funn_er_sveipens`), og lesedøra gir `kan_lukkes` med
    hver rad så flaten slipper å kopiere den.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett` — samme
presedens som 096/100–125.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_AVVIK = 500
MAKS_REGLER = 200
MAKS_TILTAK = 200
MAKS_TEKST = 4000
MAKS_NAVN = 500
MAKS_REF = 200
#: Beskrivelsen må være SKREVET. Seksten tegn er ikke en
#: kvalitetsgaranti — det er en terskel mot «feil» som eneste innhold
#: i en melding noen skal handle på (124s MIN_FORMAAL, samme form).
MIN_BESKRIVELSE = 16

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 127.
KRAVGRENSER = {
    "oppbevaring_maks_dogn": (30, 21900),
    "oppbevaringsvarsel_dogn": (1, 365),
    "tiltaksfrist_dogn": (1, 365),
    "regelvarsel_dogn": (1, 730),
}

AVVIKSTYPER = ("naerulykke", "personskade", "sykdom", "materiell",
               "psykososialt", "varsel")
MELDERFORMER = ("navngitt", "anonym")

_M53_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m53:hms")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M53_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _tekst_valgfri(kropp, felt: str, rid, maks: int) -> str | None:
    if kropp.get(felt) is None:
        return None
    return _tekst(kropp, felt, rid, maks)


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


def _boolsk(kropp, felt: str, rid) -> bool:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _dato(kropp, felt: str, rid) -> str:
    """ISO-dato som STRENG. Døra parser den; API-et validerer formen.

    `datetime.date.fromisoformat` godtar ikke «2026-13-01», og en dato
    frosset ved import ville råtnet med kalenderen (124s CodeRabbit-
    funn).
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


def _beskrivelse(kropp, felt: str, rid) -> str:
    """Ikke-tom OG lang nok.

    En melding på tre tegn er ikke en melding noen kan handle på, og et
    HMS-mottak som tok imot «feil» ville vært en postkasse.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_TEKST)
    if len(verdi) < MIN_BESKRIVELSE:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(request.path_params[navn])
    except (KeyError, ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    Uten denne ville hvert lovlige nekt i 127 blitt en 500 — og en 500
    på «du kan ikke slette dette ennå» er en feilmelding ingen kan
    handle på (121–124s form, og 123s `str.isalpha()`-lærdom om at et
    galt svar er verre enn et tregt).
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
# LESEVEIENE.
# ---------------------------------------------------------------------

def _bilderad(r) -> dict:
    return {
        "avvik": r[0], "apne": r[1], "ubehandlet_over_frist": r[2],
        "anonyme": r[3], "med_helseopplysninger": r[4],
        "levende": r[5], "oppbevaring_passert": r[6],
        "oppbevaring_naer": r[7], "regler": r[8],
        "gyldige_regler": r[9], "apne_funn": r[10],
        "har_krav": r[11],
        # ALLE FIRE GRENSENE. 123 lærte at et skjema som viser mindre
        # enn det lagrer er en felle: flaten forhåndsutfyller herfra,
        # og en grense som ikke kom med ville blitt overskrevet med
        # standardverdien første gang noen lagret.
        "oppbevaring_maks_dogn": r[12],
        "oppbevaringsvarsel_dogn": r[13],
        "tiltaksfrist_dogn": r[14], "regelvarsel_dogn": r[15],
        "kravversjon": r[16],
    }


def _avviksrad(r) -> dict:
    return {
        "avvik_id": str(r[0]), "avvikstype": r[1], "melderform": r[2],
        "beskrivelse": r[3], "sted": r[4],
        "hendelsesdato": r[5].isoformat(),
        "meldt_dato": r[6].isoformat(), "status": r[7],
        "behandlet_av": r[8], "regelversjon": r[9],
        "oppbevaring_hjemmel": r[10],
        "oppbevaring_til": r[11].isoformat(),
        "dogn_til_oppbevaring": r[12], "helseopplysninger": r[13],
        "melder_navn": r[14], "anonymisert": r[15],
        "m30_sak_ref": r[16], "antall_tiltak": r[17],
    }


def _regelrad(r) -> dict:
    return {
        "regel_id": str(r[0]), "avvikstype": r[1], "versjon": r[2],
        "hjemmel": r[3], "oppbevaring_dogn": r[4],
        "helseopplysninger": r[5], "gyldig_fra": r[6].isoformat(),
        "gyldig_til": r[7].isoformat() if r[7] else None,
        "gyldig_naa": r[8], "dogn_til_utlop": r[9],
        "antall_avvik": r[10],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1],
        "regel_id": str(r[2]) if r[2] else None,
        "avvik_id": str(r[3]) if r[3] else None,
        "over_grense": r[4], "detalj": r[5], "kravversjon": r[6],
        "forst_sett": r[7].isoformat(),
        "sist_sett_sveip": r[8].isoformat(), "apen": r[9],
        "lukket_ts": r[10].isoformat() if r[10] else None,
        "lukket_av": r[11], "lukkenotat": r[12], "kan_lukkes": r[13],
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124s form).

    Flaten tegner sammendraget, funnene, avvikene og regelverket i
    samme runde, og fire runder ville gitt fire mulige halvtegnede
    skjermer — og en flate der funnene og avvikene kunne komme fra
    ulike øyeblikk.
    """
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m53_bildet(%s)",
                         (tenant,)).fetchone()),
        "avvik": [_avviksrad(r) for r in conn.execute(
            "SELECT * FROM m53_avvikene(%s,%s)",
            (tenant, MAKS_AVVIK)).fetchall()],
        "regelverk": [_regelrad(r) for r in conn.execute(
            "SELECT * FROM m53_regelverket(%s)", (tenant,)).fetchall()],
        # BARE DE ÅPNE. En funnliste som viste de lukkede med ville
        # vokst til den ble uleselig, og de lukkede er historikk som
        # hører hjemme i et oppslag, ikke på forsiden.
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m53_funnene(%s,%s)",
            (tenant, True)).fetchall()],
    }


def hmsbilde(tjeneste, request):
    """GET /v1/hms (okonomi:read) — tenantens eget avviksregister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def avvik_endepunkt(tjeneste, request):
    """GET /v1/hms/avvik (okonomi:read).

    `melder_navn` ER `null` I TO HELT ULIKE TILFELLER, og `melderform`
    er det eneste som skiller dem:

      * `anonym` — navnet ble aldri skrevet. Det finnes ingen rad.
      * `navngitt` + `anonymisert: true` — navnet ER slettet.

    En flate som slo dem sammen ville fortalt en varsler at systemet
    «har slettet» noe det aldri hadde.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m53_avvikene(%s,%s)",
                             (auth.tenant, MAKS_AVVIK)).fetchall()
        svar = {"request_id": rid, "vist": len(rader),
                "grense": MAKS_AVVIK,
                "avvik": [_avviksrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def grunnlag_endepunkt(tjeneste, request):
    """GET /v1/hms/avvik/{avvik_id}/oppbevaringsgrunnlag (okonomi:read).

    SETNINGEN SAKSBEHANDLEREN LIMER INN i M-30s
    `personvernsak.avvist_begrunnelse`.

    `docs/M53-M30-GRENSESNITTET.md`: M-30 kan svare ja eller nei på en
    slettesak, men ikke «ja til fire lagre og nei til det femte, med
    hjemmel» — og GDPR art. 17 nr. 3 bokstav b gjør nettopp det delte
    svaret til det RIKTIGE. Vi utvider ikke M-30; vi gjør avslaget
    siterbart i det øyeblikket det skrives.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        aid = _sti_uuid(request, "avvik_id", rid)
        try:
            r = conn.execute(
                "SELECT * FROM m53_oppbevaringsgrunnlag(%s,%s)",
                (auth.tenant, aid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        svar = {"avvik_id": str(aid), "request_id": rid,
                "hjemmel": r[0], "oppbevaring_til": r[1].isoformat(),
                "regelversjon": r[2], "helseopplysninger": r[3],
                "kan_anonymiseres_naa": r[4], "dogn_igjen": r[5],
                "alt_anonymisert": r[6], "setning": r[7]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def regelverk_endepunkt(tjeneste, request):
    """GET /v1/hms/regelverk (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m53_regelverket(%s)",
                             (auth.tenant,)).fetchall()
        svar = {"request_id": rid,
                "regelverk": [_regelrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def tiltak_endepunkt(tjeneste, request):
    """GET /v1/hms/avvik/{avvik_id}/tiltak (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        aid = _sti_uuid(request, "avvik_id", rid)
        rader = conn.execute("SELECT * FROM m53_tiltakene(%s,%s)",
                             (auth.tenant, aid)).fetchall()
        svar = {"avvik_id": str(aid), "request_id": rid, "tiltak": [
            {"tiltak_id": str(r[0]), "beskrivelse": r[1],
             "lukker": r[2], "utfort_dato": r[3].isoformat(),
             "opprettet": r[4].isoformat(), "opprettet_av": r[5]}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/hms/funn (okonomi:read).

    `kan_lukkes` KOMMER FRA BASEN. Tre funntyper lukkes ikke av et
    menneske, og regelen bor ÉTT sted (`m53_funn_er_sveipens`).
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m53_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid,
                "funn": [_funnrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen fem av de seks skriveveiene deler.

    `/avvik` bruker den IKKE: den returnerer en RAD med
    oppbevaringsfristen, hjemmelen og regelversjonen — den som melder
    et avvik skal se hvor lenge opplysningen blir stående, og med
    hvilken hjemmel, i det øyeblikket den meldes.
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
    """POST /v1/hms/krav (bestilling:opprett, idem).

    HVOR LENGE VI KAN OPPBEVARE ER TENANTENS BESLUTNING. Et bygg- og
    anleggsforetak og et regnskapskontor har ikke samme risikobilde, og
    et tak vi satte for dem ville vært en fullmakt modulen ga seg selv
    over kundens etterlevelse.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        maks = _heltall(kropp, "oppbevaring_maks_dogn", rid,
                        *KRAVGRENSER["oppbevaring_maks_dogn"])
        varsel = _heltall(kropp, "oppbevaringsvarsel_dogn", rid,
                          *KRAVGRENSER["oppbevaringsvarsel_dogn"])
        tiltak = _heltall(kropp, "tiltaksfrist_dogn", rid,
                          *KRAVGRENSER["tiltaksfrist_dogn"])
        regel = _heltall(kropp, "regelvarsel_dogn", rid,
                         *KRAVGRENSER["regelvarsel_dogn"])
        return ("SELECT versjon FROM"
                " m53_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, maks, varsel, tiltak, regel, bid, nokkel),
                {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_regel_endepunkt(tjeneste, request):
    """POST /v1/hms/regelverk (bestilling:opprett, idem).

    EN ALT AVVIKLET REGELVERSJON KAN REGISTRERES: arkivet skal kunne
    svare på hvilken regel som gjaldt den gangen. Skillet går ved
    AVVIKET — `/avvik` nekter mot en versjon som ikke gjelder i dag
    (124s form).
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        avvikstype = _valg(kropp, "avvikstype", rid, AVVIKSTYPER)
        versjon = _tekst(kropp, "versjon", rid, MAKS_NAVN)
        hjemmel = _tekst(kropp, "hjemmel", rid, MAKS_NAVN)
        dogn = _heltall(kropp, "oppbevaring_dogn", rid, 1, 21900)
        helse = _boolsk(kropp, "helseopplysninger", rid)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        rid_ = _utled("regel", tenant, nokkel)
        return ("SELECT m53_registrer_regel(%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s)",
                (tenant, rid_, avvikstype, versjon, hjemmel, dogn,
                 helse, fra, til, bid),
                {"regel_id": str(rid_)}, "ny")
    return _skriv(tjeneste, request, bygg)


def meld_avvik_endepunkt(tjeneste, request):
    """POST /v1/hms/avvik (bestilling:opprett, idem).

    DEN VIKTIGSTE LINJEN I HELE MODULEN STÅR HER:

        aktor = None if melderform == "anonym" else bid

    `_browserkontekst` gir bruker-id-en. For et anonymt avvik sendes
    den ALDRI videre — ikke maskert, ikke tømt, ikke sendt.
    `revisjonslogg` er append-only siden 001, og et navn som lekker inn
    der kan aldri fjernes igjen.

    IDEMPOTENSNØKKELEN BÆRER HELLER IKKE AKTØREN. `_utled` hasher
    tenant og nøkkel, ikke bruker-id-en — ellers ville to anonyme avvik
    fra samme person hatt beslektede id-er.
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
        avvikstype = _valg(kropp, "avvikstype", rid, AVVIKSTYPER)
        melderform = _valg(kropp, "melderform", rid, MELDERFORMER)
        beskrivelse = _beskrivelse(kropp, "beskrivelse", rid)
        sted = _tekst(kropp, "sted", rid, MAKS_NAVN)
        hendelsesdato = _dato(kropp, "hendelsesdato", rid)
        navn = _tekst_valgfri(kropp, "melder_navn", rid, MAKS_NAVN)
        rolle = _tekst_valgfri(kropp, "melder_rolle", rid, MAKS_NAVN)
        anonym = melderform == "anonym"

        # VARSLERVERNET, I ÉN LINJE.
        aktor = None if anonym else bid
        # …og navnet følger med bare når det er lov å ha det. Døra
        # nekter uansett; API-et sender det ikke engang, slik at et
        # anonymt avvik aldri får et navn over ledningen.
        if anonym:
            navn = None
            rolle = None

        aid = _utled("avvik", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m53_meld_avvik(%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s)",
                (tenant, aid, avvikstype, melderform, beskrivelse,
                 sted, hendelsesdato, navn, rolle, aktor)).fetchone()
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
        return _ok({"avvik_id": str(aid), "melderform": melderform,
                    "oppbevaring_til": r[0].isoformat(),
                    "oppbevaring_hjemmel": r[1],
                    "regelversjon": r[2], "helseopplysninger": r[3],
                    "kravversjon": r[4], "melder_lagret": r[5]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_tiltak_endepunkt(tjeneste, request):
    """POST /v1/hms/avvik/{avvik_id}/tiltak (bestilling:opprett, idem).

    DEN ENESTE VEIEN FRA `apen` TIL `behandlet`. Det finnes ingen rute
    som setter statusen uten et tiltak å vise til, og ingen automatikk
    som gjør det i det hele tatt (`modulen_lukket_avvik_selv`).
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        aid = _sti_uuid(request_, "avvik_id", rid)
        beskrivelse = _beskrivelse(kropp, "beskrivelse", rid)
        lukker = _boolsk(kropp, "lukker", rid)
        utfort = _dato(kropp, "utfort_dato", rid)
        tid = _utled("tiltak", tenant, nokkel)
        return ("SELECT status FROM"
                " m53_registrer_tiltak(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, aid, tid, beskrivelse, lukker, utfort, bid),
                {"avvik_id": str(aid), "tiltak_id": str(tid)},
                "status")
    return _skriv(tjeneste, request, bygg)


def anonymiser_endepunkt(tjeneste, request):
    """POST /v1/hms/avvik/{avvik_id}/anonymiser (bestilling:opprett).

    ANONYMISERING, IKKE SLETTING. Raden blir et spor av en behandling,
    ikke en person. At vi HAR hatt avviket er nøyaktig det
    Arbeidstilsynet etterprøver; sletting ville fjernet beviset på at
    vi hadde det.

    FØR OPPBEVARINGSFRISTEN KREVES EN M-30-SAK. Døra nekter uten, og
    henvisningen skrives på raden — se
    `docs/M53-M30-GRENSESNITTET.md`.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        aid = _sti_uuid(request_, "avvik_id", rid)
        ref = _tekst_valgfri(kropp, "m30_sak_ref", rid, MAKS_REF)
        return ("SELECT anonymisert FROM"
                " m53_anonymiser(%s,%s,%s,%s)",
                (tenant, aid, ref, bid),
                {"avvik_id": str(aid)}, "anonymisert")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/hms/funn/{funn_id}/lukk (bestilling:opprett, idem).

    TRE FUNNTYPER NEKTES, og regelen bor i basen. Se
    `m53_funn_er_sveipens` — lesedøra gir `kan_lukkes` med hver rad så
    flaten slipper å kopiere den.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        fid = _sti_uuid(request_, "funn_id", rid)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        return ("SELECT m53_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, notat, bid),
                {"funn_id": str(fid), "lukket": True}, None)
    return _skriv(tjeneste, request, bygg)
