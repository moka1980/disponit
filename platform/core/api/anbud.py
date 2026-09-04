"""M-46 anbuds- og konkurransevaktens API (migrasjon 118).

Tretten endepunkter: seks leseveier og sju skriveveier, alle mot
dører. Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_anbud_eier`-eid SECURITY DEFINER-dør i 118, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN SENDER INGEN TILBUD, og det er ikke en sjekk her — det er
fraværet av en kolonne i 118. Det finnes ingen `sendt`, ingen
innsendingsrute og ingen utgående kanal i koden. Grunnen er skarpere
enn de andre fire i klyngen: et innsendt tilbud er BINDENDE, og
fristen gjør det irreversibelt på den måten som betyr noe — man kan
ikke trekke det og sende et bedre etterpå.

Det finnes derimot `/klart`, som setter en tilstand HOS OSS: et
menneske sier at utkastet er ferdig fra modulens side.

HVERT FAKTAPUNKT PEKER PÅ ET KILDEDOKUMENT, og det er heller ikke en
sjekk her: `utkastpunkt` har ingen fritekstkolonne som kan bære en
påstand. `POST /punkt` tar `kilde_id` og `sitat` — det finnes ingen
vei å sende inn en påstand uten kilde, fordi kolonnen ikke finnes.

OG ET UDEKKET KRAV BLIR ET FUNN. `POST /klart` NEKTER så lenge et
absolutt krav står udekket, og returnerer antallet VEKTEDE krav som
fortsatt mangler — så den som merker klart får vite hva de sender
uten, i stedet for å tro at alt er dekket.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som 101 innførte og
    klynge 3–6 gjenbrukte.
  * SKRIVINGEN bærer `bestilling:opprett` — presedensen fra 096/100–117.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes deterministisk av
Idempotency-Key-en. For punktet er det nødvendig: en gjentatt POST må
ikke bli to påstander på samme krav — og unikhetsvakten i 118 ville
uansett stoppet den andre, med en mindre ærlig feilmelding.
"""
from __future__ import annotations

import datetime
import uuid as uuidlib

import psycopg

MAKS_ANBUD = 200
MAKS_TITTEL = 500
MAKS_REF = 200
MAKS_TEKST = 4000
MAKS_SITAT = 4000
MAKS_NACE = 40
MAKS_LISTE = 50

#: Grensene API-et håndhever før døra. Verdiene MÅ speile CHECK-ene i
#: 118, så en feilformet request får `request_feilformet` og ikke en
#: CHECK-violation forkledd som konflikt.
KRAVGRENSER = {
    "frist_varsel_dogn": (1, 365),
    "kilde_gyldig_dogn": (1, 3650),
}
#: Øre. Speiler `maks_verdi_ore`-CHECK-en i 118.
MAKS_ORE = 100_000_000_000

ANBUDSKILDER = ("doffin", "ted", "direkte", "annen")
KRAVTYPER = ("kvalifikasjon", "dokumentasjon", "erfaring",
             "sertifisering", "okonomi", "annet")
DOKUMENTTYPER = ("sertifikat", "attest", "regnskap", "referanse",
                 "policy", "cv", "annet")
FUNNTYPER = ("frist_naermer_seg", "frist_passert",
             "udekket_absolutt_krav", "udekket_krav", "utlopt_kilde",
             "ingen_krav_registrert", "utenfor_profil",
             "ingen_profil")

_M46_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m46:anbud")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M46_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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


def _ore_valgfri(kropp, felt: str, rid) -> int | None:
    """Anslått verdi. NULL er et ÆRLIG svar: et anbud uten oppgitt
    verdi er ikke et gratisanbud, og 0 ville sagt at det var det."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if isinstance(verdi, bool) or not isinstance(verdi, int):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if verdi < 0 or verdi > MAKS_ORE:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _bool(kropp, felt: str, rid) -> bool:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _liste(kropp, felt: str, rid, maks_element: int) -> list[str]:
    """En ikke-tom liste med trimmede strenger. TOM LISTE ER FORBUDT:
    en søkeprofil uten næringskoder ville gjort hvert anbud irrelevant
    og dermed skjult alle (112s dom)."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, list) or not verdi \
            or len(verdi) > MAKS_LISTE:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for e in verdi:
        if not isinstance(e, str):
            raise _Avbrudd(_feil("request_feilformet", rid))
        e = e.strip()
        if not e or len(e) > maks_element:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if e not in ut:
            ut.append(e)
    return ut


def _tidspunkt(kropp, felt: str, rid) -> str:
    """ISO-tidspunkt som TEKST; basen caster."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.datetime.fromisoformat(verdi)
    except ValueError:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _dato_valgfri(kropp, felt: str, rid) -> str | None:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.date.fromisoformat(verdi)
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
    """Dørenes dommer → API-feil. Samme form som 112/114/116/117."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if ("ref_unik" in str(e) or "sum_unik" in str(e)
                or "nummer_unikt" in str(e)
                or "krav_unikt" in str(e)
                or "versjon_unik" in str(e)):
            return _Avbrudd(_feil("anbud_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: et udekket absolutt krav, en utløpt
        # kilde, et krav fra et annet anbud, et klart utkast.
        return _Avbrudd(_feil("anbud_ulovlig_tilstand", rid, 409))
    if isinstance(e, (psycopg.errors.IntegrityConstraintViolation,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.InsufficientPrivilege)):
        # Vaktenes dommer, blant dem nektet mot å lukke et udekket
        # absolutt krav bort.
        return _Avbrudd(_feil("anbud_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Anbudsflatens tilstand i én transaksjon, gjennom fire dører."""
    s = conn.execute("SELECT * FROM m46_anbudsstatus(%s)",
                     (tenant,)).fetchone()
    anbud = [
        {"anbud_id": str(r[0]), "ekstern_ref": r[1], "kilde": r[2],
         "tittel": r[3], "oppdragsgiver": r[4], "nace_kode": r[5],
         "geografi": r[6], "verdi_ore": r[7],
         "frist": r[8].isoformat(), "aktiv": r[9],
         "dogn_til_frist": r[10], "antall_krav": r[11],
         "absolutte_krav": r[12], "udekkede_absolutte": r[13],
         "siste_utkast": r[14], "klar": r[15], "apne_funn": r[16]}
        for r in conn.execute("SELECT * FROM m46_anbudene(%s,%s)",
                              (tenant, MAKS_ANBUD)).fetchall()]
    kilder = [
        {"kilde_id": str(r[0]), "tittel": r[1], "dokumenttype": r[2],
         "gyldig_til": r[3].isoformat() if r[3] else None,
         "innhold_sha256": r[4], "registrert": r[5].isoformat(),
         "registrert_av": r[6], "gyldig_naa": r[7],
         "brukt_i_punkter": r[8]}
        for r in conn.execute("SELECT * FROM m46_kildene(%s)",
                              (tenant,)).fetchall()]
    p = conn.execute("SELECT * FROM m46_profilen(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "anbud": s[0], "aktive": s[1], "med_utkast": s[2],
            "klare": s[3],
            # DET VIKTIGSTE TALLET. Et absolutt krav uten
            # dokumentasjon fører til AVVISNING av tilbudet.
            "udekkede_absolutte": s[4],
            "naermeste_frist": s[5].isoformat() if s[5] else None,
            "apne_funn": s[6], "kilder": s[7],
            "utlopte_kilder": s[8], "har_profil": s[9],
            "profilversjon": s[10], "vist": len(anbud)},
        "anbud": anbud,
        "kilder": kilder,
        "profil": None if p is None else {
            "nace_koder": list(p[0] or ()), "geografi": list(p[1] or ()),
            "min_verdi_ore": p[2], "maks_verdi_ore": p[3],
            "frist_varsel_dogn": p[4], "kilde_gyldig_dogn": p[5],
            "versjon": p[6], "oppdatert": p[7].isoformat(),
            "oppdatert_av": p[8]}}


def anbudsbilde(tjeneste, request):
    """GET /v1/anbud (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def krav_endepunkt(tjeneste, request):
    """GET /v1/anbud/{anbud_id}/krav (okonomi:read).

    UDEKKEDE KRAV STÅR I SAMME LISTE SOM DEKKEDE, med `punkt_id` NULL.
    En flate som bare viste de dekkede ville skjult nettopp det som må
    gjøres — og det er det udekkede absolutte kravet som gjør et tilbud
    avvist.

    `?utkast` velger hvilket utkast dekningen måles mot. Uten den måles
    ingenting som dekket, og det er riktig: dekning er en egenskap ved
    ET utkast, ikke ved anbudet.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        aid = _sti_uuid(request, "anbud_id", rid)
        rå = request.query_params.get("utkast")
        uid = None
        if rå:
            try:
                uid = uuidlib.UUID(rå)
            except ValueError:
                from .policyadmin_http import _Avbrudd, _feil
                raise _Avbrudd(_feil("request_feilformet", rid))
        rader = conn.execute("SELECT * FROM m46_kravene(%s,%s,%s)",
                             (auth.tenant, aid, uid)).fetchall()
        svar = {"anbud_id": str(aid),
                "utkast_id": str(uid) if uid else None,
                "request_id": rid,
                "krav": [
                    {"krav_id": str(r[0]), "kravnummer": r[1],
                     "kravtekst": r[2], "kravtype": r[3],
                     "absolutt": r[4],
                     "punkt_id": str(r[5]) if r[5] else None,
                     "sitat": r[6], "sidereferanse": r[7],
                     "kilde_id": str(r[8]) if r[8] else None,
                     "kildetittel": r[9],
                     "kilde_gyldig_til":
                         r[10].isoformat() if r[10] else None}
                    for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def utkast_endepunkt(tjeneste, request):
    """GET /v1/anbud/{anbud_id}/utkast (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        aid = _sti_uuid(request, "anbud_id", rid)
        rader = conn.execute("SELECT * FROM m46_utkastene(%s,%s)",
                             (auth.tenant, aid)).fetchall()
        svar = {"anbud_id": str(aid), "request_id": rid,
                "utkast": [
                    {"utkast_id": str(r[0]), "versjon": r[1],
                     "klar_til_gjennomgang": r[2],
                     "klar_ts": r[3].isoformat() if r[3] else None,
                     "klar_av": r[4], "opprettet": r[5].isoformat(),
                     "opprettet_av": r[6], "antall_punkter": r[7]}
                    for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def kilder_endepunkt(tjeneste, request):
    """GET /v1/anbud/kilder (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m46_kildene(%s)",
                             (auth.tenant,)).fetchall()
        svar = {"request_id": rid, "kilder": [
            {"kilde_id": str(r[0]), "tittel": r[1],
             "dokumenttype": r[2],
             "gyldig_til": r[3].isoformat() if r[3] else None,
             "innhold_sha256": r[4], "registrert": r[5].isoformat(),
             "registrert_av": r[6], "gyldig_naa": r[7],
             "brukt_i_punkter": r[8]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/anbud/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m46_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "funn": [
            {"anbud_id": str(r[0]), "ekstern_ref": r[1],
             "tittel": r[2], "frist": r[3].isoformat(),
             "funntype": r[4], "over_grense": r[5], "detalj": r[6],
             "profilversjon": r[7], "forst_sett": r[8].isoformat(),
             "sist_sett_sveip": r[9].isoformat(), "apen": r[10],
             "lukket_ts": r[11].isoformat() if r[11] else None}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen alle sju skriveveiene deler."""
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
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        # `felt is None` merker VOID-dørene presist (111s lærdom).
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def profil_endepunkt(tjeneste, request):
    """POST /v1/anbud/profil (bestilling:opprett, idem).

    SØKEPROFILEN ER TENANTENS — invarianten `sokeprofil_hardkodet`.
    NACE, geografi og verdigrenser er forretningsvalg: hvilke
    konkurranser man i det hele tatt vil se er ikke noe en modul kan
    bestemme.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        nace = _liste(kropp, "nace_koder", rid, MAKS_NACE)
        geografi = _liste(kropp, "geografi", rid, MAKS_REF)
        min_ore = _heltall(kropp, "min_verdi_ore", rid, 0, MAKS_ORE)
        maks_ore = _heltall(kropp, "maks_verdi_ore", rid, 0, MAKS_ORE)
        if maks_ore < min_ore:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        frist = _heltall(kropp, "frist_varsel_dogn", rid,
                         *KRAVGRENSER["frist_varsel_dogn"])
        kilde = _heltall(kropp, "kilde_gyldig_dogn", rid,
                         *KRAVGRENSER["kilde_gyldig_dogn"])
        return ("SELECT m46_sett_profil(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, nace, geografi, min_ore, maks_ore, frist,
                 kilde, bid),
                {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_anbud_endepunkt(tjeneste, request):
    """POST /v1/anbud (bestilling:opprett, idem).

    REGISTRERES MANUELT. v1 henter ingenting fra Doffin eller TED:
    portalene er ikke ETT oppslag, de er et ABONNEMENT — en søkeprofil
    som kjører kontinuerlig og henter alt som matcher. Doktrinen om den
    unødvendige forespørselen gjelder med full tyngde, og vi vet ennå
    ikke hvilke søk som er nødvendige.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        kilde = _valg(kropp, "kilde", rid, ANBUDSKILDER)
        tittel = _tekst(kropp, "tittel", rid, MAKS_TITTEL)
        oppdragsgiver = _tekst(kropp, "oppdragsgiver", rid,
                               MAKS_TITTEL)
        nace = _tekst(kropp, "nace_kode", rid, MAKS_NACE)
        geografi = _tekst(kropp, "geografi", rid, MAKS_REF)
        verdi = _ore_valgfri(kropp, "verdi_ore", rid)
        frist = _tidspunkt(kropp, "frist", rid)
        aid = _utled("anbud", tenant, nokkel)
        return ("SELECT m46_registrer_anbud("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s)",
                (tenant, aid, ref, kilde, tittel, oppdragsgiver, nace,
                 geografi, verdi, frist, bid),
                {"anbud_id": str(aid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_krav_endepunkt(tjeneste, request):
    """POST /v1/anbud/{anbud_id}/krav (bestilling:opprett, idem).

    `absolutt` ER PÅKREVD OG HAR INGEN STANDARDVERDI. Forskjellen
    avgjør om et udekket krav er en ulempe eller en AVVISNING, og en
    standardverdi ville tatt den vurderingen fra den som leser
    konkurransegrunnlaget.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        aid = _sti_uuid(request, "anbud_id", rid)
        nummer = _tekst(kropp, "kravnummer", rid, MAKS_REF)
        tekst = _tekst(kropp, "kravtekst", rid, MAKS_TEKST)
        kravtype = _valg(kropp, "kravtype", rid, KRAVTYPER)
        absolutt = _bool(kropp, "absolutt", rid)
        kid = _utled("krav", tenant, nokkel)
        return ("SELECT m46_registrer_krav(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, kid, aid, nummer, tekst, kravtype, absolutt,
                 bid),
                {"krav_id": str(kid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_kilde_endepunkt(tjeneste, request):
    """POST /v1/anbud/kilde (bestilling:opprett, idem).

    INNHOLDSSUMMEN ER PÅKREVD. Uten den kan ingen etterpå vise at det
    var NØYAKTIG denne versjonen av sertifikatet som ble sitert — og et
    anbudssvar er et dokument man blir holdt til.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        tittel = _tekst(kropp, "tittel", rid, MAKS_TITTEL)
        dokumenttype = _valg(kropp, "dokumenttype", rid,
                             DOKUMENTTYPER)
        gyldig_til = _dato_valgfri(kropp, "gyldig_til", rid)
        sum_ = _sha256(kropp, "innhold_sha256", rid)
        did = _utled("kilde", tenant, nokkel)
        return ("SELECT m46_registrer_kilde("
                "%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, did, tittel, dokumenttype, gyldig_til, sum_,
                 bid),
                {"kilde_id": str(did)}, None)
    return _skriv(tjeneste, request, bygg)


def opprett_utkast_endepunkt(tjeneste, request):
    """POST /v1/anbud/{anbud_id}/utkast (bestilling:opprett, idem).

    VERSJONEN SENDES IKKE INN — døra regner den. En kaller som fikk
    oppgi den kunne gjenbrukt et nummer og skrevet over historikken.
    """
    def bygg(tenant, bid, nokkel, _kropp, rid, request):
        aid = _sti_uuid(request, "anbud_id", rid)
        uid = _utled("utkast", tenant, nokkel)
        return ("SELECT m46_opprett_utkast(%s,%s,%s,%s)",
                (tenant, uid, aid, bid),
                {"utkast_id": str(uid)}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_punkt_endepunkt(tjeneste, request):
    """POST /v1/anbud/utkast/{utkast_id}/punkt (bestilling:opprett).

    DOM 2: HVERT FAKTAPUNKT PEKER PÅ ET KILDEDOKUMENT.

    Det finnes ingen vei her til å sende inn en påstand UTEN kilde —
    ikke fordi API-et sjekker det, men fordi `utkastpunkt` ikke har en
    kolonne å legge den i. `kilde_id` er en NOT NULL fremmednøkkel.

    Og `sitat` er et SITAT, ikke en omskrivning: en modul som
    formulerte om ville lagt til en påstand ingen kan spore tilbake til
    dokumentet.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        uid = _sti_uuid(request, "utkast_id", rid)
        krav_id = _kropp_uuid(kropp, "krav_id", rid)
        kilde_id = _kropp_uuid(kropp, "kilde_id", rid)
        sitat = _tekst(kropp, "sitat", rid, MAKS_SITAT)
        side = _tekst(kropp, "sidereferanse", rid, MAKS_REF)
        pid = _utled("punkt", tenant, nokkel)
        return ("SELECT m46_registrer_punkt(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, pid, uid, krav_id, kilde_id, sitat, side,
                 bid),
                {"punkt_id": str(pid)}, None)
    return _skriv(tjeneste, request, bygg)


def merk_klart_endepunkt(tjeneste, request):
    """POST /v1/anbud/utkast/{utkast_id}/klart (bestilling:opprett).

    «KLART TIL GJENNOMGANG» ER IKKE «SENDT». Det er en tilstand HOS
    OSS: et menneske sier at utkastet er ferdig fra modulens side. Hva
    som skjer videre — om noen laster det ned og sender det inn i
    portalen — er utenfor modulen, og det skal det være.

    DØRA NEKTER SÅ LENGE ET ABSOLUTT KRAV STÅR UDEKKET, og det er her
    «udekkede krav blir unntak, aldri utfylt gjetning» får tenner.

    SVARET BÆRER ANTALLET VEKTEDE KRAV SOM FORTSATT MANGLER. Den som
    merker klart skal vite hva de sender uten, i stedet for å tro at
    alt er dekket.
    """
    def bygg(tenant, bid, _nokkel, _kropp, rid, request):
        uid = _sti_uuid(request, "utkast_id", rid)
        return ("SELECT m46_merk_klart(%s,%s,%s)",
                (tenant, uid, bid),
                {"utkast_id": str(uid)}, "udekkede_vektede")
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/anbud/{anbud_id}/aktiv (bestilling:opprett)."""
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        aid = _sti_uuid(request, "anbud_id", rid)
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m46_sett_anbudaktiv(%s,%s,%s,%s)",
                (tenant, aid, aktiv, bid),
                {"anbud_id": str(aid), "aktiv": aktiv}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/anbud/{anbud_id}/funn/lukk (bestilling:opprett).

    ET UDEKKET ABSOLUTT KRAV KAN IKKE LUKKES HER. Døra nekter det, av
    samme grunn som M-49s bekreftede treff: et absolutt krav uten
    dokumentasjon fører til avvisning av tilbudet, og en knapp som
    gjorde den observasjonen borte ville sett ut som saksbehandling.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        aid = _sti_uuid(request, "anbud_id", rid)
        funntype = _valg(kropp, "funntype", rid, FUNNTYPER)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m46_lukk_funn(%s,%s,%s,%s,%s)",
                (tenant, aid, funntype, notat, bid),
                {"anbud_id": str(aid), "funntype": funntype}, None)
    return _skriv(tjeneste, request, bygg)
