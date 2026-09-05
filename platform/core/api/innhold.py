"""M-20 nettside- og innholdsagentens API (migrasjon 134).

Fjorten endepunkter: fem leseveier og ni skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_innhold_eier`-eid SECURITY DEFINER-dør i 134, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM PUBLISERER PÅ EGEN HÅND.

`POST /publiser` KREVER `publisert_av`, og kolonnen er `NOT NULL` i
basen. En publisering uten et menneske bak er ikke en publisering
modulen skrev ned — det er en publisering modulen GJORDE.

KLYNGENS DELTE DOM: EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS
TILBAKE — OG DEN SOM LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

En rollback fjerner siden. Den fjerner ikke at noen leste den og
handlet på den.

FEM NEKT PÅ ÉN DØR, OG ALLE FØR RADEN FINNES:

  * `POST /publiser` NEKTER når utkastet ikke er `klar`.

  * NEKTER når forhåndsvisningen gjelder et annet utkast. Det som ble
    sett er da ikke det som ville blitt publisert.

  * NEKTER når summene spriker. Vernet er egentlig sterkere enn dette:
    utkastet er append-only, så en endring er en NY versjon uten
    visning. Sammenligningen står som vern mot en fremtidig migrasjon
    som gjør utkastet muterbart.

  * NEKTER på en for gammel forhåndsvisning. Et menneske som så noe
    for tre uker siden har ikke sett dette.

  * NEKTER når en påstand hviler på en kilde som har utløpt siden
    utkastet ble merket klart.

TRE TILSTANDER ER UREPRESENTERBARE, IKKE VALIDERT: en påstand uten
kilde, en publisering uten forhåndsvisning, og en publisering uten vei
tilbake. API-et validerer dem ikke fordi det ikke KAN lage dem.

KILDEREGISTERET ER HUSETS, IKKE MODULENS. `POST /kilde` skriver i
`kildedokument` (M-46, migrasjon 118). To kilderegistre ville gitt to
svar på «kan vi belegge dette».

SCOPENE. LESING `security:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `security:read` og IKKE `okonomi:read`: en produktpåstand
med sitt kildedokument er et etterprøvbarhetsspørsmål, ikke et
finansielt. Samme vurdering som M-53 gjorde for HMS-avvik og M-7 for
referater.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_SIDER = 200
MAKS_PUBLISERINGER = 500
MAKS_FUNN = 200
MAKS_TEKST = 8000
MAKS_NAVN = 500
#: Et utkasts innhold. Stort, fordi en side ER stor — men ikke ubegrenset:
#: en JSONB uten tak er en minnegrense forkledd som en funksjon.
MAKS_INNHOLD = 400_000
#: Antall påstander i ett kall. En liste over dette er en importfeil.
MAKS_PAASTANDER = 500

DOKUMENTTYPER = ("sertifikat", "attest", "regnskap", "referanse",
                 "policy", "cv", "annet", "testrapport", "maaling",
                 "datablad", "leverandorerklaering")

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 134.
KRAVGRENSER = {
    "kilde_gyldig_dogn": (1, 3650),
    "visning_gyldig_min": (1, 20160),
    "varselfrist_dogn": (0, 365),
}

_M20_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL,
                        "disponit:m20:innhold")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M20_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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


def _heltall_valgfri(kropp, felt: str, rid, minst: int, mest: int):
    if kropp.get(felt) is None:
        return None
    return _heltall(kropp, felt, rid, minst, mest)


def _sha256(kropp, felt: str, rid) -> str:
    """EN SUM ER EN SUM, ikke en tekst som ser ut som en.

    Kolonnen har den samme CHECK-en. Denne står her fordi en 500 på et
    feilformet felt er en feilmelding ingen kan handle på.
    """
    import re
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not re.fullmatch(r"[0-9a-f]{64}",
                                                      verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _dato_valgfri(kropp, felt: str, rid):
    """ISO-dato som STRENG. Døra parser den; API-et validerer formen.

    En dato frosset ved import ville råtnet med kalenderen (124s
    CodeRabbit-funn), så `date.today()` står ingen steder her.
    """
    import datetime
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.date.fromisoformat(verdi)
    except ValueError as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e
    return verdi


def _innhold(kropp, rid):
    """Utkastets innhold som JSON. ET OBJEKT, IKKE HVA SOM HELST.

    En liste eller en streng ville gått gjennom `jsonb`, men da er
    «hva er overskriften på denne siden» ikke lenger et spørsmål med
    et svar.
    """
    import json
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get("innhold")
    if not isinstance(verdi, dict) or not verdi:
        raise _Avbrudd(_feil("request_feilformet", rid))
    kanon = json.dumps(verdi, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)
    if len(kanon) > MAKS_INNHOLD:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return kanon


def _side_id(kropp, rid) -> str:
    """SIDE-ID-EN ER EN STI, ikke en fritekst.

    Basen har den samme CHECK-en (`^[a-z0-9][a-z0-9_/-]*$`). Den står
    her også fordi en side-id er det ENESTE feltet i modulen som en
    bruker skriver fritt OG som brukes til å slå opp historikk.
    """
    import re
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get("side_id")
    if (not isinstance(verdi, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_/-]{0,200}", verdi)):
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


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    Uten denne ville hvert lovlige nekt i 134 blitt en 500 — og en 500
    på «forhåndsvisningen er eldre enn 60 minutter» er en feilmelding
    ingen kan handle på (121-133s form).

    `InsufficientPrivilege` STÅR I LISTEN fordi husets tenantvakt
    (`krev_tenantkontekst`, 038) reiser nettopp den når et kall ber om
    en annen tenants data — og det er kallerens feil.
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
        "sider": r[0], "utkast": r[1], "klare": r[2],
        "publiserte": r[3], "levende_sider": r[4], "paastander": r[5],
        "kilder": r[6], "utlopte_kilder": r[7],
        # DET DYRESTE TALLET I MODULEN: en påstand som står ute nå og
        # hviler på et dokument som ikke lenger gjelder.
        "paastander_paa_utlopt_kilde": r[8],
        "visninger": r[9], "apne_funn": r[10], "har_krav": r[11],
        # ALLE TRE GRENSENE (123s lærdom): et skjema som viser mindre
        # enn det lagrer er en felle.
        "kilde_gyldig_dogn": r[12], "visning_gyldig_min": r[13],
        "varselfrist_dogn": r[14], "kravversjon": r[15],
    }


def _siderad(r) -> dict:
    return {
        "side_id": r[0], "siste_versjon": r[1],
        "siste_utkast_id": str(r[2]), "siste_status": r[3],
        "levende_versjon": r[4],
        "levende_publisert": r[5].isoformat() if r[5] else None,
        "levende_publisert_av": r[6],
        "antall_paastander": r[7],
        # DEN SOM SER EN SIDE, SKAL SE OM DEN HVILER PÅ NOE UTLØPT —
        # uten et klikk til.
        "antall_utlopte_kilder": r[8],
        "antall_visninger": r[9],
    }


def _paastandsrad(r) -> dict:
    return {
        "paastand_id": str(r[0]), "rekkefolge": r[1], "tekst": r[2],
        # KILDEN STÅR I SAMME RAD SOM PÅSTANDEN. Et oppslag til ville
        # gjort det mulig å lese påstanden uten å se hva den hviler på.
        "kilde_id": str(r[3]), "kilde_tittel": r[4],
        "dokumenttype": r[5],
        # SUMMEN SLIK DEN VAR DA PÅSTANDEN BLE SKREVET.
        "kilde_sha256": r[6],
        "kilde_gyldig_til": r[7].isoformat() if r[7] else None,
        "kilde_gyldig": r[8],
        "registrert": r[9].isoformat(), "registrert_av": r[10],
    }


def _kilderad(r) -> dict:
    return {
        "kilde_id": str(r[0]), "tittel": r[1], "dokumenttype": r[2],
        "gyldig_til": r[3].isoformat() if r[3] else None,
        "gyldig": r[4], "dogn_igjen": r[5],
        "innhold_sha256": r[6], "registrert": r[7].isoformat(),
        "registrert_av": r[8], "antall_paastander": r[9],
    }


def _publiseringsrad(r) -> dict:
    return {
        "publisering_id": str(r[0]), "side_id": r[1], "versjon": r[2],
        "publisert_ts": r[3].isoformat(), "publisert_av": r[4],
        # VEIEN TILBAKE, SLIK DEN BLE REGNET UT DA VEIEN FRAM BLE TATT.
        "rollbackform": r[5], "rollback_til_versjon": r[6],
        "tilbake_ts": r[7].isoformat() if r[7] else None,
        "tilbake_av": r[8], "levende": r[9],
        # HVA MENNESKET SÅ, OG NÅR.
        "vist_ts": r[10].isoformat(), "vist_for": r[11],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1],
        "referanse": str(r[2]), "detaljer": r[3],
        "over_grense": r[4], "apen": r[5],
        "forst_sett": r[6].isoformat(), "sist_sett": r[7].isoformat(),
        "lukket_av": r[8],
        # HVEM SOM KAN LUKKE HVA, LEST FRA BASEN — ikke utledet i
        # flaten av skrivescopet (132s CodeRabbit-funn).
        "kan_lukkes": r[9],
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127/128/130/132/133s form)."""
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m20_bildet(%s)",
                         (tenant,)).fetchone()),
        "sider": [_siderad(r) for r in conn.execute(
            "SELECT * FROM m20_sideregister(%s,%s)",
            (tenant, MAKS_SIDER)).fetchall()],
        "kilder": [_kilderad(r) for r in conn.execute(
            "SELECT * FROM m20_kildene(%s)", (tenant,)).fetchall()],
        "publiseringer": [_publiseringsrad(r) for r in conn.execute(
            "SELECT * FROM m20_publiseringene(%s,%s)",
            (tenant, MAKS_PUBLISERINGER)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m20_innholdsfunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def innholdsbilde(tjeneste, request):
    """GET /v1/innhold (security:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def sider_endepunkt(tjeneste, request):
    """GET /v1/innhold/sider (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m20_sideregister(%s,%s)",
                             (auth.tenant, MAKS_SIDER)).fetchall()
        return kanonisk_json(
            {"sider": [_siderad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def _visningsrad(r) -> dict:
    return {
        "visning_id": str(r[0]), "vist_hash": r[1],
        "vist_ts": r[2].isoformat(), "vist_for": r[3],
        # HVILKEN VISNING SOM GJELDER DETTE INNHOLDET. Den som velger
        # skal se det, ikke oppdage det av et nekt.
        "gjelder_dette_innholdet": r[4],
    }


def utkast_endepunkt(tjeneste, request):
    """GET /v1/innhold/utkast/{utkast_id} (security:read).

    PÅSTANDENE MED SINE KILDER, OG UTKASTETS EGNE FORHÅNDSVISNINGER.

    VISNINGENE HØRER HJEMME HER OG INGEN ANDRE STEDER. Første utkast
    lot flaten lete etter dem i publiseringslisten, og det var galt på
    to måter: en side som publiseres FØR FØRSTE GANG har ingen
    publiseringer i det hele tatt, og en publiseringsrad bærer
    `vist_ts`/`vist_for` men ikke id-en publiseringsdøra trenger.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        uid = _sti_uuid(request, "utkast_id", rid)
        rader = conn.execute("SELECT * FROM m20_utkastet(%s,%s)",
                             (auth.tenant, uid)).fetchall()
        visninger = conn.execute("SELECT * FROM m20_visningene(%s,%s)",
                                 (auth.tenant, uid)).fetchall()
        return kanonisk_json(
            {"utkast_id": str(uid),
             "paastander": [_paastandsrad(r) for r in rader],
             "visninger": [_visningsrad(r) for r in visninger],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def kilder_endepunkt(tjeneste, request):
    """GET /v1/innhold/kilder (security:read) — husets kilderegister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m20_kildene(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"kilder": [_kilderad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/innhold/funn (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m20_innholdsfunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen seks av de ni skriveveiene deler.

    TRE STÅR UTENFOR, og hver av dem fordi svaret er mer enn en
    kvittering:

      * `/publiser` og `/tilbake` returnerer VEIEN TILBAKE. Den som
        publiserer skal se hva rollbacken vil gjøre i samme svar — ikke
        måtte slå det opp etterpå — og den som ruller tilbake skal se
        hva som faktisk skjedde.
      * `/klar` svarer `endret`, ikke `ok`: et gjentatt kall på et
        utkast som alt er klart er en suksess uten endring, og kalleren
        skal kunne se forskjellen.
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
    """POST /v1/innhold/krav (bestilling:opprett, idem).

    ALLE TRE GRENSENE ER TENANTENS. Hvor lenge et datablad står seg, og
    hvor lenge en forhåndsvisning er fersk nok til å publisere på, er
    forskjellig for en nettbutikk og en legemiddelprodusent. En terskel
    låst her ville vært en påstand om hvor mye det koster å ta feil i
    en produktpåstand.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        k = _heltall(kropp, "kilde_gyldig_dogn", rid,
                     *KRAVGRENSER["kilde_gyldig_dogn"])
        v = _heltall(kropp, "visning_gyldig_min", rid,
                     *KRAVGRENSER["visning_gyldig_min"])
        f = _heltall(kropp, "varselfrist_dogn", rid,
                     *KRAVGRENSER["varselfrist_dogn"])
        del nokkel
        return ("SELECT m20_sett_krav(%s,%s,%s,%s,%s)",
                (tenant, k, v, f, bid), {}, "kravversjon")
    return _skriv(tjeneste, request, bygg)


def registrer_kilde_endepunkt(tjeneste, request):
    """POST /v1/innhold/kilde (bestilling:opprett, idem).

    SKRIVER I HUSETS KILDEREGISTER (`kildedokument`, M-46/118), ikke i
    et eget. To kilderegistre ville gitt to svar på «kan vi belegge
    dette», og det er ett for mye.

    SAMME SUM ER SAMME DOKUMENT: døra svarer med raden som finnes i
    stedet for å reise en unikhetsfeil kalleren ikke kan gjøre noe med.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        tittel = _tekst(kropp, "tittel", rid, MAKS_NAVN)
        type_ = _valg(kropp, "dokumenttype", rid, DOKUMENTTYPER)
        gyldig = _dato_valgfri(kropp, "gyldig_til", rid)
        sum_ = _sha256(kropp, "innhold_sha256", rid)
        kid = _utled("kilde", tenant, nokkel)
        return ("SELECT m20_registrer_kilde(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, kid, tittel, type_, gyldig, sum_, bid),
                {}, "kilde_id")
    return _skriv(tjeneste, request, bygg)


def registrer_utkast_endepunkt(tjeneste, request):
    """POST /v1/innhold/utkast (bestilling:opprett, idem).

    HVER VERSJON ER EN NY RAD. Det finnes ingen rute som REDIGERER et
    utkast, og fraværet av den ER porten `utkast_overskrevet`: «hva sto
    i utkastet da mennesket sa ja» må kunne besvares etterpå.

    SUMMEN OPPGIS ALDRI AV KALLEREN. Døra regner den ut av innholdet;
    en sum kalleren sendte ville vært en påstand om innholdet, ikke en
    måling av det.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        side = _side_id(kropp, rid)
        innhold = _innhold(kropp, rid)
        basert = _heltall_valgfri(kropp, "basert_pa_versjon", rid,
                                  1, 1_000_000)
        rollback = _heltall_valgfri(kropp, "rollback_av_versjon", rid,
                                    1, 1_000_000)
        uid = _utled("utkast", tenant, nokkel)
        return ("SELECT m20_registrer_utkast(%s,%s,%s,%s::jsonb,%s,%s,%s)",
                (tenant, uid, side, innhold, basert, rollback, bid),
                {"utkast_id": str(uid)}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_paastand_endepunkt(tjeneste, request):
    """POST /v1/innhold/utkast/{utkast_id}/paastand (bestilling:opprett).

    `kilde_id` ER PÅKREVD, og det er hele modulen. En påstand uten
    kilde er ikke validert bort — den er urepresenterbar: kolonnen er
    NOT NULL med fremmednøkkel til `kildedokument`.

    DØRA NEKTER PÅ EN UTLØPT KILDE, FØR RADEN FINNES. Å oppdage det i
    en nattlig sveip ville vært å oppdage en skade.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        uid = _sti_uuid(request_, "utkast_id", rid)
        rekkefolge = _heltall(kropp, "rekkefolge", rid, 1,
                              MAKS_PAASTANDER)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        kid = _kropp_uuid(kropp, "kilde_id", rid)
        pid = _utled("paastand", tenant, nokkel)
        return ("SELECT m20_registrer_paastand(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, pid, uid, rekkefolge, tekst, kid, bid),
                {}, "paastand_id")
    return _skriv(tjeneste, request, bygg)


def registrer_visning_endepunkt(tjeneste, request):
    """POST /v1/innhold/utkast/{utkast_id}/visning (bestilling:opprett).

    HVA SOM BLE VIST, ikke at det ble vist. Summen kopieres fra
    utkastet av døra.

    `vist_for` ER PÅKREVD: en forhåndsvisning uten et navn er en
    forhåndsvisning ingen har sett.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        uid = _sti_uuid(request_, "utkast_id", rid)
        for_ = _tekst(kropp, "vist_for", rid, MAKS_NAVN)
        vid = _utled("visning", tenant, nokkel)
        return ("SELECT m20_registrer_visning(%s,%s,%s,%s,%s)",
                (tenant, vid, uid, for_, bid), {}, "visning_id")
    return _skriv(tjeneste, request, bygg)


def merk_klar_endepunkt(tjeneste, request):
    """POST /v1/innhold/utkast/{utkast_id}/klar (bestilling:opprett).

    «KLAR» ER EN TILSTAND HOS OSS — modulen sier at den er ferdig, ikke
    at noen har godkjent. 118s form (`m46_merk_klart`), og den samme
    grensen.

    Døra nekter så lenge én påstand hviler på en utløpt kilde: et
    utkast som ble klart med et utløpt datablad er klart til å
    publisere en udokumentert påstand.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        _krev_idem(request, rid)
        uid = _sti_uuid(request, "utkast_id", rid)
        try:
            n = conn.execute("SELECT m20_merk_klar(%s,%s,%s)",
                             (tenant, uid, bid)).fetchone()[0]
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        # `endret` OG IKKE `ok`: et gjentatt kall på et utkast som alt
        # er klart er en SUKSESS, men ingen endring — og kalleren skal
        # kunne se forskjellen.
        return _ok({"utkast_id": str(uid), "endret": n == 1}, rid)

    return _med_conn(tjeneste, rid, kjor)


def publiser_endepunkt(tjeneste, request):
    """POST /v1/innhold/utkast/{utkast_id}/publiser (bestilling:opprett).

    MODULENS VIKTIGSTE RUTE, OG DEN ENESTE HANDLINGEN SOM NÅR ET
    PUBLIKUM.

    `publisert_av` ER PÅKREVD OG SEPARAT FRA `bid`. Den innloggede
    brukeren kalte ruten; `publisert_av` er den som SVARER FOR at siden
    står ute. På et lite hus er de den samme personen, og da skal begge
    stå — ikke én utledet av den andre.

    SVARET BÆRER VEIEN TILBAKE. Den som publiserer skal se hva en
    rollback vil gjøre i samme svar: `forrige_versjon` med et nummer,
    eller `avpublisering` for den første versjonen av en side.
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
        uid = _sti_uuid(request, "utkast_id", rid)
        vid = _kropp_uuid(kropp, "visning_id", rid)
        av = _tekst(kropp, "publisert_av", rid, MAKS_NAVN)
        pid = _utled("publisering", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m20_publiser(%s,%s,%s,%s,%s,%s)",
                (tenant, pid, uid, vid, av, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"publisering_id": str(r[0]), "rollbackform": r[1],
                    "rollback_til_versjon": r[2]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def rull_tilbake_endepunkt(tjeneste, request):
    """POST /v1/innhold/publisering/{publisering_id}/tilbake.

    DØRA FINNER IKKE UT NOE NYTT. Den går veien som ble frosset da
    siden ble publisert, og bærer navnet på den som gikk den.

    TRE UTFALL, OG DET TREDJE ER DET INTERESSANTE:

      * `avpublisert` — siden var den første av sitt slag.
      * `forrige_gjenopprettet` — den gamle står ute igjen, som en NY
        periode med et nytt navn på.
      * `forrige_ikke_gjenopprettet` — den gamle sidens kilde har
        utløpt i mellomtiden, så den blir stående avpublisert.

    Å nekte tilbakerullingen i det tredje tilfellet ville låst huset
    til den nye siden det nettopp ville bort fra. Å gjenopprette ville
    publisert en udokumentert påstand. Tomrommet er det eneste av de
    tre som ikke påstår noe — og `grunn` sier hvorfor.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        _krev_idem(request, rid)
        kropp = _kropp(request)
        pid = _sti_uuid(request, "publisering_id", rid)
        av = _tekst(kropp, "tilbake_av", rid, MAKS_NAVN)
        try:
            r = conn.execute(
                "SELECT * FROM m20_rull_tilbake(%s,%s,%s,%s)",
                (tenant, pid, av, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"utfall": r[0], "versjon": r[1], "grunn": r[2]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/innhold/funn/{funn_id}/lukk (bestilling:opprett, idem).

    DØRA NEKTER PÅ SVEIPENS EGNE. `publisert_paastand_uten_gyldig_kilde`
    lukkes av at TILSTANDEN opphører — kilden fornyes eller siden
    avpubliseres — ikke av at noen huker av. En udokumentert påstand
    som står ute slutter ikke å stå ute fordi noen leste varselet.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        fid = _sti_uuid(request_, "funn_id", rid)
        grunn = _tekst(kropp, "grunn", rid, MAKS_TEKST)
        return ("SELECT m20_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, grunn, bid),
                {"funn_id": str(fid)}, "lukket")
    return _skriv(tjeneste, request, bygg)
