"""M-51 tilskudds- og støtteordningsvaktens API (migrasjon 119).

Fjorten endepunkter: seks leseveier og åtte skriveveier, alle mot
dører. Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_tilskudd_eier`-eid SECURITY DEFINER-dør i 119, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN SENDER INGEN SØKNAD, og det er ikke en sjekk her — 119 har
ingen «sendt»-kolonne. `/ferdigstill` setter en tilstand HOS OSS.

ET ESTIMAT ER ET TALL EN BEDRIFT PLANLEGGER ETTER, og det er derfor
vakten skiller så skarpt mellom estimat og lovnad. Sier vi «dere kan få
400 000», og bedriften ansetter på det grunnlaget, er avstanden ikke
akademisk: den er lønnsutbetalinger.

DERFOR TO FRAVÆR OG ÉN NEKT:

  * DET FINNES INGEN RUTE SOM SETTER ET BELØP DIREKTE. Estimatets sum
    er summen av poster, og hver post går gjennom
    `POST /post` med en `kildepost_id`. `tilskuddsestimat` har ingen
    beløpskolonne å skrive til.

  * DET FINNES INGEN «SEND SØKNAD»-RUTE.

  * `/ferdigstill` NEKTER uten minst én forutsetning, og uten en eneste
    post. Svaret bærer summen OG spennet — nedre og øvre grense — så
    den som ferdigstiller ser hva estimatet faktisk sier.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett` — samme
presedens som 096/100–118.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en. For
posten er det nødvendig: en gjentatt POST må ikke bli to andeler på
samme kildepost — dobbelttelling er nettopp feilen som gjør en
tilskuddssak til en tilbakebetalingssak.
"""
from __future__ import annotations

import datetime
import uuid as uuidlib

import psycopg

MAKS_ORDNINGER = 200
MAKS_KILDEPOSTER = 300
MAKS_TEKST = 4000
MAKS_NAVN = 500
MAKS_REF = 200

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 119.
KRAVGRENSER = {
    "frist_varsel_dogn": (1, 365),
    "kildepost_gyldig_dogn": (1, 3650),
    "usikkerhet_prosent": (0, 100),
}
#: Øre. BIGINT-taket brukt av `maks_belop_ore` og beløpsfeltene.
MAKS_ORE = 100_000_000_000

SYSTEMER = ("regnskap", "lonn", "timeforing", "faktura", "manuell")
FORUTSETNINGSARTER = ("regelverk", "regnskapstall", "bemanning",
                      "aktivitet", "annet")
FUNNTYPER = ("frist_naermer_seg", "frist_passert",
             "estimat_uten_poster", "estimat_over_ordningstak",
             "utdatert_kildepost", "ingen_estimat", "ingen_krav")

_M51_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m51:tilskudd")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M51_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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


def _ore(kropp, felt: str, rid) -> int:
    """DOM 4: ØRE, HELTALL — invarianten `belop_i_flyttall`.

    Et estimat regnet i flyttall gir en bedrift et tall som ikke
    stemmer med regnskapet de søker på grunnlag av, og avviket dukker
    opp først når forvalteren kontrollregner.
    """
    return _heltall(kropp, felt, rid, 0, MAKS_ORE)


def _ore_valgfri(kropp, felt: str, rid) -> int | None:
    """NULL er et ærlig svar: en ordning uten tak har ikke et tak på
    null, og et oppdiktet stort tall ville sett ut som et tak."""
    if kropp.get(felt) is None:
        return None
    return _ore(kropp, felt, rid)


def _prosent_valgfri(kropp, felt: str, rid) -> int | None:
    if kropp.get(felt) is None:
        return None
    return _heltall(kropp, felt, rid, 0, 100)


def _dato(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.date.fromisoformat(verdi)
    except ValueError:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _tidspunkt(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.datetime.fromisoformat(verdi)
    except ValueError:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sha256(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip().lower()
    if len(verdi) != 64 or any(c not in "0123456789abcdef"
                               for c in verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _bool(kropp, felt: str, rid) -> bool:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _kropp_uuid(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _doerfeil(e, rid):
    """Dørenes dommer → API-feil. Samme form som 112–118."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if ("versjon_unik" in str(e) or "ref_unik" in str(e)
                or "kilde_unik" in str(e)):
            return _Avbrudd(_feil("tilskudd_ulovlig_tilstand", rid,
                                  409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: en andel større enn kildeposten, en
        # kildepost utenfor perioden, et estimat uten forutsetninger.
        return _Avbrudd(_feil("tilskudd_ulovlig_tilstand", rid, 409))
    if isinstance(e, (psycopg.errors.IntegrityConstraintViolation,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.InsufficientPrivilege)):
        return _Avbrudd(_feil("tilskudd_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Tilskuddsflatens tilstand i én transaksjon, gjennom fire dører."""
    s = conn.execute("SELECT * FROM m51_tilskuddsstatus(%s)",
                     (tenant,)).fetchone()
    ordninger = [
        {"ordning_id": str(r[0]), "ordningskode": r[1], "navn": r[2],
         "forvalter": r[3], "regelverksversjon": r[4],
         "maks_belop_ore": r[5], "sats_prosent": r[6],
         "soknadsfrist": r[7].isoformat(), "aktiv": r[8],
         "dogn_til_frist": r[9], "siste_estimat": r[10],
         "estimat_id": str(r[11]) if r[11] else None, "klar": r[12],
         # SUM OG SPENN REGNES I BASEN, i heltall. To lesere skal ikke
         # kunne komme til hver sin konklusjon om hva et estimat sier.
         "sum_ore": r[13], "nedre_ore": r[14], "ovre_ore": r[15],
         "antall_poster": r[16], "antall_forutsetninger": r[17],
         "apne_funn": r[18]}
        for r in conn.execute("SELECT * FROM m51_ordningene(%s,%s)",
                              (tenant, MAKS_ORDNINGER)).fetchall()]
    kildeposter = [
        {"kildepost_id": str(r[0]), "system": r[1],
         "ekstern_ref": r[2], "beskrivelse": r[3], "belop_ore": r[4],
         "periode_fra": r[5].isoformat(),
         "periode_til": r[6].isoformat(),
         "registrert": r[7].isoformat(), "registrert_av": r[8],
         "fersk": r[9], "brukt_i_poster": r[10]}
        for r in conn.execute("SELECT * FROM m51_kildepostene(%s,%s)",
                              (tenant, MAKS_KILDEPOSTER)).fetchall()]
    k = conn.execute("SELECT * FROM m51_kravene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "ordninger": s[0], "aktive": s[1], "med_estimat": s[2],
            "klare": s[3],
            # SUMMEN AV DE KLARE ESTIMATENE. Tallet en bedrift
            # planlegger etter, og derfor det som må være riktig.
            "sum_klare_ore": s[4],
            "naermeste_frist": s[5].isoformat() if s[5] else None,
            "apne_funn": s[6], "kildeposter": s[7],
            "utdaterte_kildeposter": s[8], "har_krav": s[9],
            "kravversjon": s[10], "vist": len(ordninger)},
        "ordninger": ordninger,
        "kildeposter": kildeposter,
        "krav": None if k is None else {
            "frist_varsel_dogn": k[0], "kildepost_gyldig_dogn": k[1],
            "usikkerhet_prosent": k[2], "versjon": k[3],
            "oppdatert": k[4].isoformat(), "oppdatert_av": k[5]}}


def tilskuddsbilde(tjeneste, request):
    """GET /v1/tilskudd (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def estimater_endepunkt(tjeneste, request):
    """GET /v1/tilskudd/{ordning_id}/estimater (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        oid = _sti_uuid(request, "ordning_id", rid)
        rader = conn.execute("SELECT * FROM m51_estimatene(%s,%s)",
                             (auth.tenant, oid)).fetchall()
        svar = {"ordning_id": str(oid), "request_id": rid,
                "estimater": [
                    {"estimat_id": str(r[0]), "versjon": r[1],
                     "periode_fra": r[2].isoformat(),
                     "periode_til": r[3].isoformat(),
                     "usikkerhet_prosent": r[4], "kravversjon": r[5],
                     "klar_til_gjennomgang": r[6],
                     "klar_ts": r[7].isoformat() if r[7] else None,
                     "klar_av": r[8], "opprettet": r[9].isoformat(),
                     "opprettet_av": r[10], "sum_ore": r[11],
                     "antall_poster": r[12],
                     "antall_forutsetninger": r[13]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def poster_endepunkt(tjeneste, request):
    """GET /v1/tilskudd/estimat/{estimat_id}/poster (okonomi:read).

    HVER RAD VISER HVOR TALLET KOM FRA — system, referanse og
    kildepostens EGET beløp — så andelen kan etterprøves uten å slå opp
    noe annet sted. Uten det ville «andel 40 000» vært et tall uten
    kontekst.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        eid = _sti_uuid(request, "estimat_id", rid)
        rader = conn.execute("SELECT * FROM m51_postene(%s,%s)",
                             (auth.tenant, eid)).fetchall()
        svar = {"estimat_id": str(eid), "request_id": rid,
                "poster": [
                    {"post_id": str(r[0]),
                     "kildepost_id": str(r[1]), "system": r[2],
                     "ekstern_ref": r[3], "beskrivelse": r[4],
                     "kilde_belop_ore": r[5], "andel_ore": r[6],
                     "begrunnelse": r[7],
                     "periode_fra": r[8].isoformat(),
                     "periode_til": r[9].isoformat(),
                     "registrert": r[10].isoformat(),
                     "registrert_av": r[11]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def forutsetninger_endepunkt(tjeneste, request):
    """GET /v1/tilskudd/estimat/{estimat_id}/forutsetninger.

    FORUTSETNINGENE ER DET SOM GJØR ESTIMATET TIL ET ESTIMAT. Hver har
    en KONSEKVENS: «faller bort helt» og «reduseres med ca. 30 %» er to
    helt forskjellige beskjeder til den som planlegger.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        eid = _sti_uuid(request, "estimat_id", rid)
        rader = conn.execute(
            "SELECT * FROM m51_forutsetningene(%s,%s)",
            (auth.tenant, eid)).fetchall()
        svar = {"estimat_id": str(eid), "request_id": rid,
                "forutsetninger": [
                    {"forutsetning_id": str(r[0]), "art": r[1],
                     "tekst": r[2], "konsekvens": r[3],
                     "registrert": r[4].isoformat(),
                     "registrert_av": r[5]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def kildeposter_endepunkt(tjeneste, request):
    """GET /v1/tilskudd/kildeposter (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m51_kildepostene(%s,%s)",
                             (auth.tenant,
                              MAKS_KILDEPOSTER)).fetchall()
        svar = {"request_id": rid, "kildeposter": [
            {"kildepost_id": str(r[0]), "system": r[1],
             "ekstern_ref": r[2], "beskrivelse": r[3],
             "belop_ore": r[4], "periode_fra": r[5].isoformat(),
             "periode_til": r[6].isoformat(),
             "registrert": r[7].isoformat(), "registrert_av": r[8],
             "fersk": r[9], "brukt_i_poster": r[10]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/tilskudd/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m51_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "funn": [
            {"ordning_id": str(r[0]), "ordningskode": r[1],
             "navn": r[2], "soknadsfrist": r[3].isoformat(),
             "funntype": r[4], "over_grense": r[5], "detalj": r[6],
             "sum_ore": r[7], "kravversjon": r[8],
             "forst_sett": r[9].isoformat(),
             "sist_sett_sveip": r[10].isoformat(), "apen": r[11],
             "lukket_ts": r[12].isoformat() if r[12] else None}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen sju av de åtte skriveveiene deler.

    `/ferdigstill` bruker den IKKE: den returnerer en RAD (sum, spenn,
    tellinger), ikke en skalar.
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
        # `felt is None` merker VOID-dørene presist (111s lærdom).
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def krav_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/krav (bestilling:opprett, idem).

    TENANTENS EGNE TERSKLER. Usikkerheten særlig: hvor forsiktig man
    vil være med et estimat er en forretningsbeslutning, ikke noe en
    modul kan bestemme.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        frist = _heltall(kropp, "frist_varsel_dogn", rid,
                         *KRAVGRENSER["frist_varsel_dogn"])
        kilde = _heltall(kropp, "kildepost_gyldig_dogn", rid,
                         *KRAVGRENSER["kildepost_gyldig_dogn"])
        usikkerhet = _heltall(kropp, "usikkerhet_prosent", rid,
                              *KRAVGRENSER["usikkerhet_prosent"])
        # NØKKELEN GÅR HELT INN I DØRA. `tilskuddskrav` er en singleton
        # per tenant og har ingen id å utlede fra nøkkelen slik de
        # andre skrivedørene gjør — så uten dette ville et gjenspill
        # etter en tidsavbrutt forbindelse bumpet `versjon` en gang
        # til, og hvert funn bærer `kravversjon`.
        return ("SELECT m51_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, frist, kilde, usikkerhet, bid, nokkel), {},
                "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_ordning_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/ordning (bestilling:opprett, idem).

    REGELVERKSVERSJONEN OG INNHOLDSSUMMEN ER PÅKREVD. Et regelverk som
    endres gjør gårsdagens estimat feil uten at noe i systemet vet det
    — og uten versjon kan ingen etterpå si hvilke regler estimatet ble
    regnet mot.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        kode = _tekst(kropp, "ordningskode", rid, MAKS_REF)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        forvalter = _tekst(kropp, "forvalter", rid, MAKS_NAVN)
        versjon = _tekst(kropp, "regelverksversjon", rid, MAKS_REF)
        sum_ = _sha256(kropp, "regelverk_sha256", rid)
        maks = _ore_valgfri(kropp, "maks_belop_ore", rid)
        sats = _prosent_valgfri(kropp, "sats_prosent", rid)
        frist = _tidspunkt(kropp, "soknadsfrist", rid)
        oid = _utled("ordning", tenant, nokkel)
        return ("SELECT m51_registrer_ordning("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s)",
                (tenant, oid, kode, navn, forvalter, versjon, sum_,
                 maks, sats, frist, bid),
                {"ordning_id": str(oid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_kildepost_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/kildepost (bestilling:opprett, idem).

    DER TALLENE KOMMER FRA. Systemet og referansen er påkrevd: et tall
    uten opphav er et tall ingen kan etterprøve, og en tilskuddssak
    kontrolleres i ettertid.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        system = _valg(kropp, "system", rid, SYSTEMER)
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        beskrivelse = _tekst(kropp, "beskrivelse", rid, MAKS_TEKST)
        belop = _ore(kropp, "belop_ore", rid)
        fra = _dato(kropp, "periode_fra", rid)
        til = _dato(kropp, "periode_til", rid)
        kid = _utled("kildepost", tenant, nokkel)
        return ("SELECT m51_registrer_kildepost("
                "%s,%s,%s,%s,%s,%s,%s::date,%s::date,%s)",
                (tenant, kid, system, ref, beskrivelse, belop, fra,
                 til, bid),
                {"kildepost_id": str(kid)}, None)
    return _skriv(tjeneste, request, bygg)


def opprett_estimat_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/{ordning_id}/estimat (bestilling:opprett).

    VERSJONEN OG USIKKERHETEN SENDES IKKE INN — døra regner den ene og
    kopierer den andre fra tenantens krav. Endrer tenanten usikkerheten
    i morgen, står gårsdagens estimat med sitt eget spenn.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        oid = _sti_uuid(request, "ordning_id", rid)
        fra = _dato(kropp, "periode_fra", rid)
        til = _dato(kropp, "periode_til", rid)
        eid = _utled("estimat", tenant, nokkel)
        return ("SELECT m51_opprett_estimat("
                "%s,%s,%s,%s::date,%s::date,%s)",
                (tenant, eid, oid, fra, til, bid),
                {"estimat_id": str(eid)}, "versjon")
    return _skriv(tjeneste, request, bygg)


def legg_til_post_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/estimat/{estimat_id}/post.

    DOM 2: HVERT BELØP PEKER PÅ EN KILDEPOST.

    Det finnes ingen vei her til å sette et beløp uten kilde — ikke
    fordi API-et sjekker det, men fordi `tilskuddsestimat` ikke har en
    beløpskolonne. Summen ER summen av disse radene.

    `andel_ore` er DEN ANDELEN som teller med, ikke en prosent: en
    prosent av et beløp er en utregning noen må gjøre om igjen, og
    avrundingen ville flyttet på seg mellom oss og forvalteren.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        eid = _sti_uuid(request, "estimat_id", rid)
        kilde = _kropp_uuid(kropp, "kildepost_id", rid)
        andel = _ore(kropp, "andel_ore", rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_TEKST)
        pid = _utled("post", tenant, nokkel)
        return ("SELECT m51_legg_til_post(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, pid, eid, kilde, andel, begrunnelse, bid),
                {"post_id": str(pid)}, None)
    return _skriv(tjeneste, request, bygg)


def legg_til_forutsetning_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/estimat/{estimat_id}/forutsetning.

    KONSEKVENSEN ER PÅKREVD. En forutsetning uten konsekvens er en
    ansvarsfraskrivelse, ikke en opplysning: «faller bort helt» og
    «reduseres med ca. 30 %» er to helt forskjellige beskjeder til den
    som planlegger etter tallet.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        eid = _sti_uuid(request, "estimat_id", rid)
        art = _valg(kropp, "art", rid, FORUTSETNINGSARTER)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        konsekvens = _tekst(kropp, "konsekvens", rid, MAKS_TEKST)
        if len(tekst) < 8 or len(konsekvens) < 8:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        fid = _utled("forutsetning", tenant, nokkel)
        return ("SELECT m51_legg_til_forutsetning("
                "%s,%s,%s,%s,%s,%s,%s)",
                (tenant, fid, eid, art, tekst, konsekvens, bid),
                {"forutsetning_id": str(fid)}, None)
    return _skriv(tjeneste, request, bygg)


def ferdigstill_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/estimat/{estimat_id}/ferdigstill.

    DEN ENESTE SKRIVEVEIEN SOM IKKE BRUKER `_skriv`, fordi døra
    returnerer en RAD og ikke en skalar: sum, nedre og øvre grense, og
    tellingen av poster og forutsetninger.

    DØRA NEKTER UTEN MINST ÉN FORUTSETNING. Et estimat uten
    forutsetninger ER en lovnad — ingenting sier hva tallet hviler på,
    og den som planlegger etter det kan ikke se når grunnlaget svikter.

    SVARET BÆRER SPENNET, ikke bare summen. «400 000» og «320 000 til
    480 000» er to forskjellige beskjeder, og bare den andre er et
    estimat.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        _krev_idem(request, rid)
        eid = _sti_uuid(request, "estimat_id", rid)
        try:
            rad = conn.execute(
                "SELECT * FROM m51_ferdigstill_estimat(%s,%s,%s)",
                (tenant, eid, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"estimat_id": str(eid), "sum_ore": rad[0],
                    "nedre_ore": rad[1], "ovre_ore": rad[2],
                    "antall_poster": rad[3],
                    "antall_forutsetninger": rad[4]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/{ordning_id}/aktiv (bestilling:opprett)."""
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        oid = _sti_uuid(request, "ordning_id", rid)
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m51_sett_ordningaktiv(%s,%s,%s,%s)",
                (tenant, oid, aktiv, bid),
                {"ordning_id": str(oid), "aktiv": aktiv}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/tilskudd/{ordning_id}/funn/lukk.

    ET ESTIMAT OVER ORDNINGENS TAK KAN IKKE LUKKES HER, av samme grunn
    som M-46s udekkede absolutte krav (118) og M-49s bekreftede treff
    (117): det vil bli avkortet eller avslått, og en knapp som gjorde
    den observasjonen borte ville sett ut som saksbehandling.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        oid = _sti_uuid(request, "ordning_id", rid)
        funntype = _valg(kropp, "funntype", rid, FUNNTYPER)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m51_lukk_funn(%s,%s,%s,%s,%s)",
                (tenant, oid, funntype, notat, bid),
                {"ordning_id": str(oid), "funntype": funntype}, None)
    return _skriv(tjeneste, request, bygg)
