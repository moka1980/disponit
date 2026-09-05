"""M-45 bærekrafts- og ESG-agentens API (migrasjon 136).

Femten endepunkter: seks leseveier og ni skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_esg_eier`-eid SECURITY DEFINER-dør i 136, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM SENDER RAPPORTEN.

`POST /sammenstill` samler tallene og skriver en ny rad. Det er alt.
Innsendingen til et tilsyn er et menneskes, og den hører hjemme i M-47
— en rute her ville gjort «sendte vi?» til et spørsmål med to svar.

KLYNGENS DELTE DOM: EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS
TILBAKE — OG DEN SOM LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

En bærekraftsrapport leses av investorer, kunder og et tilsyn. Et
estimat lest som en måling er grønnvasking, uansett hva som var ment.

SEKS NEKT PÅ `POST /maaling`, ALLE FØR RADEN FINNES: ukjent periode,
LUKKET periode, ukjent faktor, faktor fra en ANNEN STANDARDVERSJON enn
perioden, faktor som ikke gjaldt i perioden, og utløpt kilde.

`er_estimat` ER PÅKREVD OG HAR INGEN DEFAULT. En default ville stille
merket alt som målt, og en glemt kolonne ville blitt en FALSK PÅSTAND
i stedet for en feil. Et estimat må dessuten si HVA det hviler på.

STANDARDVERSJONEN OPPGIS ALDRI AV KALLEREN. Døra leser den fra
perioden og skriver den på raden: en kaller som fikk sette sin egen
kunne regnet fjorårets tall med årets faktor.

MENGDER SENDES SOM TEKST, IKKE SOM TALL. `json` i Python gir `float`
for et desimaltall, og en `float` flytter seg i siste desimal. Døra tar
`NUMERIC`, og API-et validerer formen og sender teksten videre.

SCOPENE. LESING `security:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `security:read`: et tall med sitt kildedokument og sin
faktorversjon er et etterprøvbarhetsspørsmål mot et tilsyn, ikke et
finansielt. Samme vurdering som M-53, M-7, M-20 og M-43.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import re
import uuid as uuidlib

import psycopg

MAKS_PERIODER = 200
MAKS_RAPPORTER = 200
MAKS_FUNN = 200
MAKS_TEKST = 8000
MAKS_NAVN = 500
#: En hjemmelsbeskrivelse — her: et estimatgrunnlag — må være SKREVET.
#: Seksten tegn er ikke en kvalitetsgaranti; det er en terskel mot
#: «anslag» som eneste begrunnelse for et tall et tilsyn skal lese.
MIN_TEKST = 16
#: Maks rekkefølgenummer på en påstand.
MAKS_PAASTANDER = 500
#: Mengden som et TALL, ikke som en flyttallsstreng. Døra tar NUMERIC,
#: og API-et sender teksten videre — en `float` i Python ville flyttet
#: seg i siste desimal på veien.
MENGDE = re.compile(r"^[0-9]{1,15}(\.[0-9]{1,6})?$")
FAKTORVERDI = re.compile(r"^[0-9]{1,11}(\.[0-9]{1,8})?$")

DOKUMENTTYPER = ("sertifikat", "attest", "regnskap", "referanse",
                 "policy", "cv", "annet", "testrapport", "maaling",
                 "datablad", "leverandorerklaering")

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 136.
KRAVGRENSER = {
    "estimatterskel_bp": (0, 10000),
    "estimatfrist_dogn": (1, 3650),
    "kilde_gyldig_dogn": (1, 3650),
}

_M45_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m45:esg")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M45_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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

    Uten denne ville hvert lovlige nekt i 136 blitt en 500 — og en 500
    på «agenten sa hva den er etter 45 sekunder» er en feilmelding
    ingen kan handle på (121-135s form).

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




def _mengde(kropp, felt: str, rid, monster) -> str:
    """ET TALL SOM TEKST, OG DET ER IKKE SLURV.

    `json.loads` gir `float` for et desimaltall, og en `float` flytter
    seg i siste desimal. En utslippsfaktor som gjorde det ville gjort
    «samme tall» til et spørsmål med to svar — i en rapport et tilsyn
    leser.

    Døra tar `NUMERIC`. API-et validerer FORMEN og sender teksten
    videre urørt.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    # En `int` er trygg og skrives om; en `float` avvises.
    if isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if isinstance(verdi, int):
        verdi = str(verdi)
    if not isinstance(verdi, str) or not monster.fullmatch(verdi.strip()):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi.strip()


def _bool(kropp, felt: str, rid) -> bool:
    """PÅKREVD, OG UTEN EN DEFAULT.

    `er_estimat` har ingen default i basen, og den har ingen her
    heller. Et felt som mangler skal stoppe skrivingen — ikke stille
    bli til «målt».
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _tekst_valgfri(kropp, felt: str, rid, maks: int):
    if kropp.get(felt) is None:
        return None
    return _tekst(kropp, felt, rid, maks)


# ---------------------------------------------------------------------
# RADBYGGERNE — ÉN FORM, ETT STED.
#
# TALLENE SENDES SOM TEKST UT OGSÅ. En `NUMERIC` som ble til `float` i
# svaret ville flyttet seg i siste desimal på vei til flaten, og da
# ville tallet på skjermen ikke vært tallet i rapporten.
# ---------------------------------------------------------------------

def _bilderad(r) -> dict:
    return {
        "perioder": r[0], "apne_perioder": r[1], "maalinger": r[2],
        "estimater": r[3], "paastander": r[4], "faktorer": r[5],
        "gjeldende_faktorer": r[6], "rapporter": r[7], "kilder": r[8],
        "utlopte_kilder": r[9], "apne_funn": r[10],
        # DET DYRESTE TALLET I MODULEN: den perioden der mest av
        # utslippet er gjettet.
        "hoyeste_estimatandel_bp": r[11],
        "har_krav": r[12],
        # ALLE TRE GRENSENE (123s lærdom).
        "estimatterskel_bp": r[13], "estimatfrist_dogn": r[14],
        "kilde_gyldig_dogn": r[15], "kravversjon": r[16],
    }


def _perioderad(r) -> dict:
    return {
        "periode_id": str(r[0]), "merke": r[1],
        "fra": r[2].isoformat(), "til": r[3].isoformat(),
        "standard": r[4],
        # VERSJONEN SOM ER LÅST. Den som ser en periode skal se hvilken
        # standard tallene i den er regnet med.
        "standardversjon": r[5], "status": r[6],
        "antall_maalinger": r[7], "antall_estimater": r[8],
        "antall_paastander": r[9], "sum_utslipp_kg": str(r[10]),
        "estimatandel_bp": r[11], "siste_rapportversjon": r[12],
        "antall_utlopte_kilder": r[13],
    }


def _maalingsrad(r) -> dict:
    return {
        "maaling_id": str(r[0]), "kategori": r[1],
        "mengde": str(r[2]), "enhet": r[3], "utslipp_kg": str(r[4]),
        # ESTIMATET, MERKET — OG MED HVA DET HVILER PÅ.
        "er_estimat": r[5], "estimatgrunnlag": r[6],
        "faktor_verdi": str(r[7]), "standardversjon": r[8],
        # KILDEN I SAMME RAD SOM TALLET (134s form).
        "kilde_tittel": r[9], "kilde_sha256": r[10],
        "kilde_gyldig": r[11],
        # ET ERSTATTET TALL ER SYNLIG SOM ERSTATTET, ikke borte.
        "erstattet": r[12], "dogn_gammelt": r[13],
        "registrert": r[14].isoformat(), "registrert_av": r[15],
    }


def _paastandsrad(r) -> dict:
    return {
        "paastand_id": str(r[0]), "rekkefolge": r[1], "tekst": r[2],
        "kilde_tittel": r[3], "dokumenttype": r[4],
        "kilde_sha256": r[5], "kilde_gyldig": r[6],
        "maaling_id": str(r[7]) if r[7] else None,
        # EN PÅSTAND SOM HVILER PÅ ET ESTIMAT BÆRER DET VIDERE.
        "maaling_er_estimat": r[8],
        "registrert": r[9].isoformat(), "registrert_av": r[10],
    }


def _faktorrad(r) -> dict:
    return {
        "faktor_id": str(r[0]), "kategori": r[1], "enhet": r[2],
        "verdi": str(r[3]), "standard": r[4],
        "standardversjon": r[5], "kilde_tittel": r[6],
        "gyldig_fra": r[7].isoformat(),
        "gyldig_til": r[8].isoformat() if r[8] else None,
        "gjelder": r[9], "antall_maalinger": r[10],
    }


def _rapportrad(r) -> dict:
    return {
        "rapport_id": str(r[0]), "periode_id": str(r[1]),
        "periodemerke": r[2], "versjon": r[3],
        "innholds_hash": r[4], "sum_utslipp_kg": str(r[5]),
        "antall_maalinger": r[6], "antall_estimater": r[7],
        # HVOR MYE AV TALLET SOM ER GJETTET, i selve rapporten.
        "estimatandel_bp": r[8], "antall_paastander": r[9],
        "standardversjon": r[10], "sammenstilt": r[11].isoformat(),
        "sammenstilt_av": r[12],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1],
        "referanse": str(r[2]), "detaljer": r[3],
        "over_grense": r[4], "apen": r[5],
        "forst_sett": r[6].isoformat(), "sist_sett": r[7].isoformat(),
        "lukket_av": r[8], "kan_lukkes": r[9],
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL."""
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m45_bildet(%s)",
                         (tenant,)).fetchone()),
        "perioder": [_perioderad(r) for r in conn.execute(
            "SELECT * FROM m45_perioderegister(%s,%s)",
            (tenant, MAKS_PERIODER)).fetchall()],
        "faktorer": [_faktorrad(r) for r in conn.execute(
            "SELECT * FROM m45_faktorene(%s)", (tenant,)).fetchall()],
        "rapporter": [_rapportrad(r) for r in conn.execute(
            "SELECT * FROM m45_rapportene(%s,%s)",
            (tenant, MAKS_RAPPORTER)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m45_esgfunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def esgbilde(tjeneste, request):
    """GET /v1/esg (security:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def perioder_endepunkt(tjeneste, request):
    """GET /v1/esg/perioder (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m45_perioderegister(%s,%s)",
                             (auth.tenant, MAKS_PERIODER)).fetchall()
        return kanonisk_json(
            {"perioder": [_perioderad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def maalinger_endepunkt(tjeneste, request):
    """GET /v1/esg/periode/{periode_id}/maalinger (security:read).

    TALLENE MED SITT GRUNNLAG, i én rad hver.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        pid = _sti_uuid(request, "periode_id", rid)
        rader = conn.execute("SELECT * FROM m45_maalingene(%s,%s)",
                             (auth.tenant, pid)).fetchall()
        return kanonisk_json(
            {"periode_id": str(pid),
             "maalinger": [_maalingsrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def paastander_endepunkt(tjeneste, request):
    """GET /v1/esg/periode/{periode_id}/paastander (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        pid = _sti_uuid(request, "periode_id", rid)
        rader = conn.execute("SELECT * FROM m45_paastandene(%s,%s)",
                             (auth.tenant, pid)).fetchall()
        return kanonisk_json(
            {"periode_id": str(pid),
             "paastander": [_paastandsrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def faktorer_endepunkt(tjeneste, request):
    """GET /v1/esg/faktorer (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m45_faktorene(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"faktorer": [_faktorrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/esg/funn (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m45_esgfunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen sju av de ni skriveveiene deler.

    TO STÅR UTENFOR: `/maaling` returnerer UTSLIPPET og
    STANDARDVERSJONEN — kalleren oppgir ingen av delene, så begge må
    komme tilbake — og `/sammenstill` returnerer ESTIMATANDELEN, fordi
    det er tallet den som sammenstiller trenger å se med én gang.
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
                psycopg.errors.DatetimeFieldOverflow,
                psycopg.errors.NumericValueOutOfRange,
                psycopg.errors.InvalidTextRepresentation) as e:
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
    """POST /v1/esg/krav (bestilling:opprett, idem).

    ALLE TRE GRENSENE ER TENANTENS. Hvor lenge et gjettet tall kan stå
    i en rapport et tilsyn leser, er en vurdering av hvor mye det
    koster å ta feil — og et lite verksted og et børsnotert konsern
    tåler ikke det samme.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        e = _heltall(kropp, "estimatterskel_bp", rid,
                     *KRAVGRENSER["estimatterskel_bp"])
        f = _heltall(kropp, "estimatfrist_dogn", rid,
                     *KRAVGRENSER["estimatfrist_dogn"])
        k = _heltall(kropp, "kilde_gyldig_dogn", rid,
                     *KRAVGRENSER["kilde_gyldig_dogn"])
        del nokkel
        return ("SELECT m45_sett_krav(%s,%s,%s,%s,%s)",
                (tenant, e, f, k, bid), {}, "kravversjon")
    return _skriv(tjeneste, request, bygg)


def registrer_kilde_endepunkt(tjeneste, request):
    """POST /v1/esg/kilde (bestilling:opprett, idem).

    SKRIVER I HUSETS KILDEREGISTER (M-46/118) for TREDJE gang: M-46
    bygde det, M-20 arvet det i 134, M-45 arver det her. Tre registre
    for «hva hviler dette på» ville gitt tre svar.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        tittel = _tekst(kropp, "tittel", rid, MAKS_NAVN)
        type_ = _valg(kropp, "dokumenttype", rid, DOKUMENTTYPER)
        gyldig = _dato_valgfri(kropp, "gyldig_til", rid)
        sum_ = _tekst(kropp, "innhold_sha256", rid, 64)
        if not re.fullmatch(r"[0-9a-f]{64}", sum_):
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        kid = _utled("kilde", tenant, nokkel)
        return ("SELECT m45_registrer_kilde(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, kid, tittel, type_, gyldig, sum_, bid),
                {}, "kilde_id")
    return _skriv(tjeneste, request, bygg)


def apne_periode_endepunkt(tjeneste, request):
    """POST /v1/esg/periode (bestilling:opprett, idem).

    STANDARDVERSJONEN OPPGIS ÉN GANG, HER, OG LÅSES.

    Det finnes ingen rute som endrer den. Fraværet ER
    `standardversjon_laast_per_periode`: en periode som kunne bytte
    versjon i ettertid ville gjort hvert tall i den til et tall regnet
    med en annen standard enn det står at det er.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        merke = _tekst(kropp, "merke", rid, MAKS_NAVN)
        fra = _dato(kropp, "fra", rid)
        til = _dato(kropp, "til", rid)
        standard = _tekst(kropp, "standard", rid, MAKS_NAVN)
        versjon = _tekst(kropp, "standardversjon", rid, MAKS_NAVN)
        pid = _utled("periode", tenant, nokkel)
        return ("SELECT m45_apne_periode(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, pid, merke, fra, til, standard, versjon, bid),
                {}, "periode_id")
    return _skriv(tjeneste, request, bygg)


def lukk_periode_endepunkt(tjeneste, request):
    """POST /v1/esg/periode/{periode_id}/lukk.

    ENVEIS. En lukket periode som kunne åpnes igjen ville tatt imot
    tall etter at rapporten var lest.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel, kropp
        pid = _sti_uuid(request_, "periode_id", rid)
        return ("SELECT m45_lukk_periode(%s,%s,%s)",
                (tenant, pid, bid), {"periode_id": str(pid)}, "lukket")
    return _skriv(tjeneste, request, bygg)


def registrer_faktor_endepunkt(tjeneste, request):
    """POST /v1/esg/faktor (bestilling:opprett, idem).

    FAKTOREN HVILER OGSÅ PÅ ET DOKUMENT. En utslippsfaktor uten kilde
    er et tall noen husket, og hele rapporten hviler på det.

    `verdi` SENDES SOM TEKST. En `float` ville flyttet seg i siste
    desimal, og da ville «samme faktor» hatt to verdier.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        kategori = _tekst(kropp, "kategori", rid, MAKS_NAVN)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,60}", kategori):
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        enhet = _tekst(kropp, "enhet", rid, MAKS_NAVN)
        verdi = _mengde(kropp, "verdi", rid, FAKTORVERDI)
        standard = _tekst(kropp, "standard", rid, MAKS_NAVN)
        versjon = _tekst(kropp, "standardversjon", rid, MAKS_NAVN)
        kid = _kropp_uuid(kropp, "kilde_id", rid)
        fra = _dato(kropp, "gyldig_fra", rid)
        fid = _utled("faktor", tenant, nokkel)
        return ("SELECT m45_registrer_faktor"
                "(%s,%s,%s,%s,%s::numeric,%s,%s,%s,%s,%s)",
                (tenant, fid, kategori, enhet, verdi, standard,
                 versjon, kid, fra, bid), {}, "faktor_id")
    return _skriv(tjeneste, request, bygg)


def avvikle_faktor_endepunkt(tjeneste, request):
    """POST /v1/esg/faktor/{faktor_id}/avvikle.

    EN RETTELSE ER EN NY FAKTOR, ikke en endring. Verdien er frosset:
    en korreksjon i ettertid ville endret hvert tall som noen gang ble
    regnet med den.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        fid = _sti_uuid(request_, "faktor_id", rid)
        til = _dato(kropp, "gyldig_til", rid)
        return ("SELECT m45_avvikle_faktor(%s,%s,%s,%s)",
                (tenant, fid, til, bid),
                {"faktor_id": str(fid)}, "avviklet")
    return _skriv(tjeneste, request, bygg)


def registrer_paastand_endepunkt(tjeneste, request):
    """POST /v1/esg/periode/{periode_id}/paastand.

    «Ingen påstand uten datagrunnlag (anti-grønnvasking).» `kilde_id`
    er påkrevd; peker påstanden også på en måling, må den høre til
    samme periode.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        pid = _sti_uuid(request_, "periode_id", rid)
        rekkefolge = _heltall(kropp, "rekkefolge", rid, 1,
                              MAKS_PAASTANDER)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        kid = _kropp_uuid(kropp, "kilde_id", rid)
        mid = _kropp_uuid_valgfri(kropp, "maaling_id", rid)
        sid = _utled("paastand", tenant, nokkel)
        return ("SELECT m45_registrer_paastand(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, sid, pid, rekkefolge, tekst, kid, mid, bid),
                {}, "paastand_id")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/esg/funn/{funn_id}/lukk.

    DØRA NEKTER PÅ SVEIPENS EGNE. Et estimat som har stått for lenge
    slutter ikke å ha stått for lenge fordi noen leste varselet.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        fid = _sti_uuid(request_, "funn_id", rid)
        grunn = _tekst(kropp, "grunn", rid, MAKS_TEKST)
        return ("SELECT m45_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, grunn, bid),
                {"funn_id": str(fid)}, "lukket")
    return _skriv(tjeneste, request, bygg)


def registrer_maaling_endepunkt(tjeneste, request):
    """POST /v1/esg/periode/{periode_id}/maaling.

    MODULENS VIKTIGSTE RUTE, OG DER TRE INVARIANTER MØTES.

    SEKS NEKT, ALLE FØR RADEN FINNES: ukjent periode, LUKKET periode,
    ukjent faktor, faktor fra en ANNEN STANDARDVERSJON enn perioden,
    faktor som ikke gjaldt i perioden, og utløpt kilde.

    `er_estimat` ER PÅKREVD OG HAR INGEN DEFAULT. Et estimat må si HVA
    det hviler på; en måling kan ikke ha et estimatgrunnlag.

    STANDARDVERSJONEN OPPGIS ALDRI. Døra leser den fra perioden, og de
    to sammensatte fremmednøklene gjør at faktoren MÅ ha den samme.

    SVARET BÆRER UTSLIPPET OG VERSJONEN, fordi kalleren oppga ingen av
    delene.
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
        pid = _sti_uuid(request, "periode_id", rid)
        kategori = _tekst(kropp, "kategori", rid, MAKS_NAVN)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,60}", kategori):
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        mengde = _mengde(kropp, "mengde", rid, MENGDE)
        enhet = _tekst(kropp, "enhet", rid, MAKS_NAVN)
        fid = _kropp_uuid(kropp, "faktor_id", rid)
        er_estimat = _bool(kropp, "er_estimat", rid)
        grunnlag = _tekst_valgfri(kropp, "estimatgrunnlag", rid,
                                  MAKS_TEKST)
        if er_estimat and (grunnlag is None or len(grunnlag) < MIN_TEKST):
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        if not er_estimat and grunnlag is not None:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        erstatter = _kropp_uuid_valgfri(kropp, "erstatter_maaling_id",
                                        rid)
        kid = _kropp_uuid(kropp, "kilde_id", rid)
        mid = _utled("maaling", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m45_registrer_maaling"
                "(%s,%s,%s,%s,%s::numeric,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, mid, pid, kategori, mengde, enhet, fid,
                 er_estimat, grunnlag, erstatter, kid, bid)).fetchone()
        except (psycopg.errors.NumericValueOutOfRange,
                psycopg.errors.InvalidTextRepresentation) as e:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"maaling_id": str(r[0]), "utslipp_kg": str(r[1]),
                    "standardversjon": r[2]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def sammenstill_endepunkt(tjeneste, request):
    """POST /v1/esg/periode/{periode_id}/sammenstill.

    OG DEN SENDER INGENTING.

    Døra samler tallene som står i perioden, regner summen og
    estimatandelen, og skriver en NY RAD. Det finnes ingen kolonne for
    «sendt» og ingen rute som setter en — innsendingen til et tilsyn er
    et menneskes, og den hører hjemme i M-47.

    SVARET BÆRER ESTIMATANDELEN. Den som sammenstiller skal se hvor mye
    av tallet som er gjettet, i samme svar — ikke oppdage det når
    tilsynet spør.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        pid = _sti_uuid(request, "periode_id", rid)
        rapport = _utled("rapport", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m45_sammenstill(%s,%s,%s,%s)",
                (tenant, rapport, pid, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"rapport_id": str(r[0]), "versjon": r[1],
                    "sum_utslipp_kg": str(r[2]),
                    "estimatandel_bp": r[3]}, rid)

    return _med_conn(tjeneste, rid, kjor)
