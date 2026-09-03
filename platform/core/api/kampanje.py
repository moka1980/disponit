"""M-44 kampanjeregisterets API (migrasjon 114).

Ti endepunkter: fire leseveier og seks skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver gjør nøyaktig ett kall mot
en `disponit_kampanje_eier`-eid SECURITY DEFINER-dør i 114, og runtime
har ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN SENDER INGENTING.

M-44 er en annen figur enn de tre andre i klynge 5. De er manglende
VERIFIKATORER — betrodde parter som skal attestere et vilkår. M-44 er
den manglende AKTØREN: netthandelsmalen fører modulen som `modul:` på
en `auto`-handling, ikke i `verifikatorer`.

Vilkårene har verifikatorer som FINNES — `v_samtykke` er M-30,
`v_prisbok` er M-26 fra klynge 4. Det er HANDLINGEN SELV som mangler en
modul.

DET GJØR TILBAKEHOLDELSEN STERKERE, IKKE SVAKERE. For de tre andre
kunne man sagt at modulen bare mangler én evne. Her finnes modulen FOR
å sende, og v1 sender null.

OG SE PÅ REVERSERINGEN MALEN FORESLÅR: `kompenserende`, med
`kampanje.send_korreksjon`. Botemiddelet for en feilsendt e-post er å
sende en til — en andre e-post til noen som ikke ville ha den første.
En utsending er irreversibel på den måten som betyr noe.

DERFOR FINNES DET INGEN UTSENDINGSDØR HER, og modulen har ingen
HTTP-klient og ingen SMTP-klient. `kampanje.py` importerer ingenting
som kan snakke ut.

KONTAKTPUNKTET LAGRES ALDRI I KLARTEKST. Det går inn én gang, døren
normaliserer det, regner masken og den saltede hashen, og kaster
adressen. SVARET ER MASKEN.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som M-13 (101) innførte
    og klynge 3, 4 og 111–113 gjenbrukte.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100–113.

SP-2 PÅ REGISTRERINGSDØRENE: `mottaker_id`, `hendelse_id` og
`kampanje_id` utledes deterministisk av Idempotency-Key-en. For
samtykkehendelsen er det strengt nødvendig: en gjentatt POST må ikke
bli to samtykker i en historikk som ER svaret på om vi hadde lov.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_MOTTAKERE = 200
MAKS_KAMPANJER = 200
MAKS_HISTORIKK = 200
MAKS_REF = 100
MAKS_NAVN = 300
MAKS_KONTAKT = 320
MAKS_LENKE = 2000
MAKS_NOTAT = 2000

#: LOVLIGE SAMTYKKETILSTANDER. `trukket` står her fordi en avmelding er
#: en hendelse man REGISTRERER — aldri en rad man sletter.
TILSTANDER = ("gitt", "bekreftet", "trukket", "utlopt_markert")

#: LOVLIGE KANALER. Hvor samtykket kom fra avgjør om det er et samtykke
#: i det hele tatt: avkryssingen i kassa og en importert liste er ikke
#: samme grunnlag, og en importert liste er ofte ikke et samtykke.
KANALER = ("kasse", "preferanseside", "skjema", "import", "manuell",
           "avmeldingslenke")

#: GRENSENES YTTERPUNKTER, ikke verdier. Speiler CHECK-ene i 114.
#: MALEN FORESLÅR 2 per uke — men det er et FORSLAG, ikke en grense
#: noen tenant har vedtatt, og det står derfor i basen og ikke her.
GRENSER = {
    "maks_per_periode": (0, 1000),
    "periode_dogn": (1, 3650),
    "samtykke_gyldig_dogn": (1, 3650),
}

_M44_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m44:kampanje")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M44_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    """Tekst, TRIMMET — og lengden måles på det som faktisk lagres."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _lenke(kropp, felt: str, rid) -> str:
    """AVMELDINGSLENKEN, og den må være `https://`.

    Ikke `http://`: en avmeldingslenke over ukryptert forbindelse lekker
    at mottakeren fikk kampanjen — til alle som ser trafikken. Basen
    sjekker det samme (114); dette er det ytre gjerdet, så feilen blir
    en 400 og ikke en 409 fra en dør.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_LENKE)
    if not verdi.startswith("https://") or len(verdi) <= len("https://"):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if any(t.isspace() for t in verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valg(kropp, felt: str, rid, lovlige) -> str:
    """Ett av et LUKKET sett."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """Et heltall, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"maks_per_periode": true}` blitt taket 1.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _bool(kropp, felt: str, rid) -> bool:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt, False)
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


_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
)


def _doerfeil(e, rid):
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if "ref_unik" in str(e) or "kilde_unik" in str(e):
            return _Avbrudd(_feil("kampanje_ulovlig_tilstand", rid, 409))
        # PK-kollisjon på en SP-2-utledet id, eller samme mottaker lagt
        # i samme plan to ganger: SAMME nøkkel, samme rad.
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: et samtykke i framtida, en avlyst
        # kampanje, en deaktivert mottaker.
        return _Avbrudd(_feil("kampanje_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Vaktenes dommer: en frosset samtykkehendelse, en gjenåpnet
        # kampanje.
        return _Avbrudd(_feil("kampanje_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Kampanjeflatens tilstand i én transaksjon, gjennom fire lesedører."""
    s = conn.execute("SELECT * FROM m44_kampanjestatus(%s)",
                     (tenant,)).fetchone()
    mottakere = [
        {"mottaker_id": str(r[0]), "ekstern_ref": r[1], "navn": r[2],
         "kontakt_maske": r[3], "aktiv": r[4], "tilstand": r[5],
         "kanal": r[6],
         "siste_samtykke": r[7].isoformat() if r[7] else None,
         "i_planer": r[8], "apne_funn": list(r[9] or ())}
        for r in conn.execute("SELECT * FROM m44_mottakerne(%s,%s)",
                              (tenant, MAKS_MOTTAKERE)).fetchall()]
    kampanjer = [
        {"kampanje_id": str(r[0]), "ekstern_ref": r[1], "navn": r[2],
         "formal": r[3], "avmeldingslenke": r[4],
         "planlagt_sendt": r[5].isoformat(), "status": r[6],
         "mottakere": r[7], "opprettet": r[8].isoformat(),
         "opprettet_av": r[9]}
        for r in conn.execute("SELECT * FROM m44_kampanjene(%s,%s)",
                              (tenant, MAKS_KAMPANJER)).fetchall()]
    g = conn.execute("SELECT * FROM m44_grensene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "mottakere": s[0], "aktive": s[1], "med_samtykke": s[2],
            "kampanjer": s[3], "planlagte": s[4], "apne_funn": s[5],
            "apne_over_tak": s[6], "har_grense": s[7],
            "grenseversjon": s[8], "vist": len(mottakere)},
        "mottakere": mottakere,
        "kampanjer": kampanjer,
        "grense": None if g is None else {
            "maks_per_periode": g[0], "periode_dogn": g[1],
            "samtykke_gyldig_dogn": g[2], "versjon": g[3],
            "oppdatert": g[4].isoformat(), "oppdatert_av": g[5]}}


def kampanjebilde(tjeneste, request):
    """GET /v1/kampanje (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def historikk_endepunkt(tjeneste, request):
    """GET /v1/kampanje/mottaker/{mottaker_id}/samtykke (okonomi:read).

    HVER SAMTYKKEHENDELSE, med sin kanal. `endret` sier hvilken linje
    som var et faktisk SKIFTE — en avmelding, en fornyet bekreftelse.

    Dette er svaret på «hadde vi lov», og det er derfor det står som en
    historikk og ikke som en tilstand.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        mid = _sti_uuid(request, "mottaker_id", rid)
        rader = conn.execute(
            "SELECT * FROM m44_samtykkehistorikken(%s,%s,%s)",
            (auth.tenant, mid, MAKS_HISTORIKK)).fetchall()
        return kanonisk_json({
            "mottaker_id": str(mid),
            "hendelser": [
                {"hendelse_id": str(r[0]), "tilstand": r[1],
                 "kanal": r[2], "kilde_ref": r[3], "formal": r[4],
                 "inntruffet": r[5].isoformat(), "notat": r[6],
                 "registrert": r[7].isoformat(), "registrert_av": r[8],
                 "endret": r[9]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def samtykke_paa_dato_endepunkt(tjeneste, request):
    """GET /v1/kampanje/mottaker/{mottaker_id}/samtykke/{dag} (read).

    «HADDE VI LOV TIL Å SENDE DETTE DEN DAGEN» — spørsmålet et tilsyn
    stiller, med sitt eget endepunkt.

    Det finnes fordi svaret ikke skal måtte utledes av en leser som
    blar i historikken: den siste hendelsen med `inntruffet <= dagen`
    ER svaret, og døren gir det direkte.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        mid = _sti_uuid(request, "mottaker_id", rid)
        dag = str(request.path_params.get("dag") or "")
        rad = conn.execute(
            "SELECT * FROM m44_samtykke_paa_dato(%s,%s,%s::date)",
            (auth.tenant, mid, dag)).fetchone()
        return kanonisk_json({
            "mottaker_id": str(mid), "dag": dag,
            "samtykke": None if rad is None else {
                "hendelse_id": str(rad[0]), "tilstand": rad[1],
                "kanal": rad[2], "kilde_ref": rad[3],
                "formal": rad[4], "inntruffet": rad[5].isoformat(),
                "registrert": rad[6].isoformat(),
                "registrert_av": rad[7]},
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen alle seks skriveveiene deler."""
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
        # ET FELT SOM ALLTID ER NULL ER ET LØFTE API-ET IKKE HOLDER.
        # `felt is None` merker VOID-dørene presist (111s lærdom).
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def grense_endepunkt(tjeneste, request):
    """POST /v1/kampanje/grense (bestilling:opprett, idem).

    TAKET ER TENANTENS. Malen FORESLÅR to per uke per mottaker — men et
    forslag i en bransjemal er ikke en grense noen tenant har vedtatt,
    og en konstant i koden ville vært nøyaktig den fullmakten
    invarianten `frekvensgrense_hardkodet` forbyr.

    ÆRLIG OM HVA DETTE IKKE ER: taket går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert og har ingen fasilitet for en
    tenant-innstilling. Invarianten er oppfylt i den forstand som betyr
    noe — tenanten eier og fører verdiene — men koblingen til M-1 står
    igjen som et NAVNGITT gap, samme gap som 111–113 navnga.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        maks = _heltall(kropp, "maks_per_periode", rid,
                        *GRENSER["maks_per_periode"])
        periode = _heltall(kropp, "periode_dogn", rid,
                           *GRENSER["periode_dogn"])
        gyldig = _heltall(kropp, "samtykke_gyldig_dogn", rid,
                          *GRENSER["samtykke_gyldig_dogn"])
        return ("SELECT m44_sett_grense(%s,%s,%s,%s,%s)",
                (tenant, maks, periode, gyldig, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_mottaker_endepunkt(tjeneste, request):
    """POST /v1/kampanje/mottaker (bestilling:opprett, idem).

    KONTAKTPUNKTET GÅR INN ÉN GANG. Døren normaliserer det, regner
    masken og den saltede hashen, og kaster adressen. Svaret er masken;
    API-et har ingen vei tilbake til adressen, og logger den ikke.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        kontakt = _tekst(kropp, "kontakt", rid, MAKS_KONTAKT)
        mid = _utled("mottaker", tenant, nokkel)
        return ("SELECT m44_registrer_mottaker(%s,%s,%s,%s,%s,%s)",
                (tenant, mid, ref, navn, kontakt, bid),
                {"mottaker_id": str(mid)}, "kontakt_maske")
    return _skriv(tjeneste, request, bygg)


def registrer_samtykke_endepunkt(tjeneste, request):
    """POST /v1/kampanje/mottaker/{mottaker_id}/samtykke (idem).

    EN AVMELDING ER EN HENDELSE MAN REGISTRERER, ikke en rad man
    sletter. `trukket` føres her som alt annet, og begge står i
    historikken etterpå — det er nettopp den historikken som svarer på
    om vi hadde lov den dagen.

    KANALEN ER OBLIGATORISK: avkryssingen i kassa og en importert liste
    er ikke samme grunnlag, og en importert liste er ofte ikke et
    samtykke i det hele tatt.

    FORMÅLET LIKESÅ: «samtykke til hva» er ubesvart uten det.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        mid = _sti_uuid(request, "mottaker_id", rid)
        tilstand = _valg(kropp, "tilstand", rid, TILSTANDER)
        kanal = _valg(kropp, "kanal", rid, KANALER)
        kilde_ref = _tekst(kropp, "kilde_ref", rid, MAKS_REF)
        formal = _tekst(kropp, "formal", rid, MAKS_NOTAT)
        inntruffet = _tekst(kropp, "inntruffet", rid, 32)
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        hid = _utled("samtykke", tenant, nokkel)
        return ("SELECT m44_registrer_samtykke("
                "%s,%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, hid, mid, tilstand, kanal, kilde_ref, formal,
                 inntruffet, notat, bid),
                {"mottaker_id": str(mid), "hendelse_id": str(hid)},
                None)
    return _skriv(tjeneste, request, bygg)


def registrer_kampanje_endepunkt(tjeneste, request):
    """POST /v1/kampanje/kampanje (bestilling:opprett, idem).

    AVMELDINGSLENKEN ER PÅKREVD, og den må være `https://`.

    Ikke fordi v1 sender — men fordi en kampanje som ikke KUNNE vært
    sendt lovlig heller ikke skal kunne stå i registeret som om den var
    klar. `avmeldingslenke` er et eget vilkår i malen, og her er det en
    NOT NULL-kolonne med en formsjekk.

    `planlagt_sendt` ER EN DATO, IKKE EN KØ. Registeret vet når
    kampanjen VAR ment å gå — og det er den datoen frekvenstaket måles
    på. Ingenting sender noe når datoen passerer.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        formal = _tekst(kropp, "formal", rid, MAKS_NOTAT)
        lenke = _lenke(kropp, "avmeldingslenke", rid)
        planlagt = _tekst(kropp, "planlagt_sendt", rid, 32)
        kid = _utled("kampanje", tenant, nokkel)
        return ("SELECT m44_registrer_kampanje("
                "%s,%s,%s,%s,%s,%s,%s::date,%s)",
                (tenant, kid, ref, navn, formal, lenke, planlagt, bid),
                {"kampanje_id": str(kid)}, None)
    return _skriv(tjeneste, request, bygg)


def avlys_kampanje_endepunkt(tjeneste, request):
    """POST /v1/kampanje/kampanje/{kampanje_id}/avlys (idem).

    EN KAMPANJE AVLYSES, DEN SLETTES ALDRI: en slettet kampanje ville
    tatt planen med seg, og mottakerne i den ville forsvunnet fra
    frekvenstellingen.
    """
    def bygg(tenant, bid, _nokkel, _kropp, rid, request):
        kid = _sti_uuid(request, "kampanje_id", rid)
        return ("SELECT m44_avlys_kampanje(%s,%s,%s)",
                (tenant, kid, bid),
                {"kampanje_id": str(kid)}, "endret")
    return _skriv(tjeneste, request, bygg)


def legg_i_plan_endepunkt(tjeneste, request):
    """POST /v1/kampanje/kampanje/{kampanje_id}/plan (idem).

    DENNE DØREN SENDER INGENTING. Den skriver ned at mottakeren VAR
    MENT å få kampanjen — og det er dét frekvenstaket måles på.

    SVARET SIER HVOR MANGE KAMPANJER MOTTAKEREN DA STÅR OPPFØRT TIL i
    tenantens periode. Den som planlegger får vite det med én gang, og
    ikke først når sveipen har gått natta etter.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        kid = _sti_uuid(request, "kampanje_id", rid)
        mid = _kropp_uuid(kropp, "mottaker_id", rid)
        return ("SELECT m44_legg_i_plan(%s,%s,%s,%s)",
                (tenant, kid, mid, bid),
                {"kampanje_id": str(kid), "mottaker_id": str(mid)},
                "i_periode")
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/kampanje/mottaker/{mottaker_id}/aktiv (idem).

    EN MOTTAKER DEAKTIVERES, HEN SLETTES ALDRI: en slettet mottaker
    ville tatt samtykkehistorikken med seg — og den er det eneste som
    kan svare på om vi hadde lov.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        mid = _sti_uuid(request, "mottaker_id", rid)
        # `aktiv` ER PÅKREVD HER. `_bool` faller tilbake til `false`, og
        # en kropp uten feltet ville derfor DEAKTIVERT mottakeren —
        # altså en utelatelse som utfører en handling (CodeRabbit, 108).
        if "aktiv" not in kropp:
            raise _Avbrudd(_feil("request_feilformet", rid))
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m44_sett_mottakeraktiv(%s,%s,%s,%s)",
                (tenant, mid, aktiv, bid),
                {"mottaker_id": str(mid)}, "endret")
    return _skriv(tjeneste, request, bygg)
