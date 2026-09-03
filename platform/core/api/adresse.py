"""M-19 adressevalideringens API (migrasjon 112).

Sju endepunkter: tre leseveier og fire skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver gjør nøyaktig ett kall mot
en `disponit_adresse_eier`-eid SECURITY DEFINER-dør i 112, og runtime
har ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN SLÅR INGENTING OPP EKSTERNT.

Netthandelsmalen navngir modulen som verifikatoren `v_adresse`, betrodd
for `adresse_validert` — og M-25s `ordre.bekreft_og_fakturer` står som
`modus: auto` gatet på nettopp det vilkåret.

Den nærliggende måten å «løse» det på ville vært et oppslag mot et
adresseregister. Det er en utgående kanal med personopplysninger i: vi
ville sendt kundens navn og adresse ut av huset, til en tredjepart vi
ikke har databehandleravtale med, for å få tilbake et ja eller nei vi
så ville kalt «validert».

Og svaret ville uansett vært feil vare. At en adresse FINNES i et
register sier ikke at pakken kommer fram til den som skal ha den.

DERFOR FINNES DET INGEN OPPSLAGSDØR HER, og modulen har ingen HTTP-
klient. `adresse.py` importerer ingenting som kan snakke ut.

NORMALISERINGEN ERSTATTER ALDRI ORIGINALEN. Begge står på raden, i hver
sin kolonne, begge frosset — og normaliseringen REGNES I BASEN, av
`m19_normaliser`. API-et sender aldri inn en normalisert form: da kunne
den vært hva som helst, og kolonnen ville sluttet å bety «det vi
faktisk sammenlignet på».

HVER KONTROLL HAR EN KILDE OG EN METODE. `metode` er et lukket sett der
INGEN AV VERDIENE ER ET OPPSLAG, og `kilde_ref` er påkrevd. En
«validert» adresse uten hvem og hvordan er ikke en måling — det er
nøyaktig den påstanden `adresse_validert` ville hvilt på.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som M-13 (101) innførte
    og klynge 3, 4 og M-41 (111) gjenbrukte.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100–111.

SP-2 PÅ REGISTRERINGSDØRENE: `subjekt_id`, `versjon_id` og
`kontroll_id` utledes deterministisk av Idempotency-Key-en. For
adressen og kontrollen er det strengt nødvendig: en gjentatt POST må
ikke bli to versjoner i en historikk som ER beviset.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_SUBJEKTER = 200
MAKS_HISTORIKK = 200
MAKS_KONTROLLER = 200
MAKS_REF = 100
MAKS_NAVN = 300
MAKS_LINJE = 200
MAKS_POSTNR = 20
MAKS_POSTSTED = 100
MAKS_NOTAT = 2000

#: LOVLIGE KILDER for en adresseversjon. «Hvor kom denne adressen fra»
#: er forskjellen mellom noe kunden står inne for og noe vi skrev.
KILDER = ("oppgitt_av_kunde", "ordre", "manuell", "import")

#: LOVLIGE KONTROLLMETODER — OG INGEN AV DEM ER ET OPPSLAG.
#:
#: Det er ikke en forglemmelse: settet ER v1-dommen, skrevet ut. Skulle
#: noen en dag legge til `oppslag` her, må de gjøre det i en migrasjon,
#: i dette settet, og i grensen `m19-v1` — tre steder, alle røde i
#: portene til noen bestemmer seg for å endre dem.
METODER = ("visuell", "bekreftet_av_kunde", "dokumentert",
           "levering_bekreftet")

#: LOVLIGE UTFALL. `ukontrollerbar` er et SVAR, ikke et fravær: en
#: kontroll som ikke lot seg gjennomføre er noe annet enn en kontroll
#: ingen har forsøkt, og noe helt annet enn et avslag.
UTFALL = ("godkjent", "avvist", "ukontrollerbar")

#: KRAVENES YTTERPUNKTER, ikke verdier. Speiler CHECK-ene i 112.
KRAVGRENSER = {
    "ukontrollert_dogn": (0, 3650),
    "kontroll_gyldig_dogn": (0, 3650),
}

_M19_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m19:adresse")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M19_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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


def _tekst_valgfri(kropp, felt: str, rid, maks: int) -> str | None:
    """Adresselinje 2 og begrunnelsen: fravær er et lovlig svar.

    En tom streng blir `None`, ikke `""` — «ingen andre adresselinje»
    og «en andre adresselinje som er tom» er ikke to tilstander noen
    skal kunne skille mellom i ettertid.
    """
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    return _tekst(kropp, felt, rid, maks)


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
    og uten sjekken ville `{"ukontrollert_dogn": true}` blitt ett døgn.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _metoder(kropp, felt: str, rid) -> list[str]:
    """METODESETTET, og det er her v1-dommen kunne vært omgått.

    Uten denne sjekken kunne en tenant skrevet «oppslag» i lista si, og
    `modulen_slo_opp_eksternt` ville vært brutt gjennom en
    konfigurasjonsverdi framfor gjennom kode. Basen sjekker det samme
    (112); dette er det ytre gjerdet, så feilen blir en 400 og ikke en
    409 fra en dør.

    TOM LISTE ER FORBUDT: et krav uten metoder gjør hver adresse til et
    funn, som ser ut som en streng policy og er en konfigurasjonsfeil.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, list) or not verdi:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if len(verdi) > len(METODER):
        raise _Avbrudd(_feil("request_feilformet", rid))
    for m in verdi:
        if m not in METODER:
            raise _Avbrudd(_feil("request_feilformet", rid))
    if len(set(verdi)) != len(verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return list(verdi)


def _land(kropp, felt: str, rid) -> str:
    """ISO 3166-1 alfa-2, STORE BOKSTAVER.

    Ikke en validering av at landet finnes — det ville krevd et
    register, og et register er et oppslag. Bare av at feltet har formen
    til en landkode.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip().upper()
    if len(verdi) != 2 or not verdi.isalpha() or not verdi.isascii():
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


_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
)


def _doerfeil(e, rid):
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if "ref_unik" in str(e) or "kilde_unik" in str(e):
            return _Avbrudd(_feil("adresse_ulovlig_tilstand", rid, 409))
        # PK-kollisjon på en SP-2-utledet id: SAMME nøkkel, samme rad.
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: en adresse fra framtida, en kontroll
        # av et deaktivert subjekt, en ukjent metode.
        return _Avbrudd(_feil("adresse_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Vaktenes dommer: en frosset versjon, et avslag uten
        # begrunnelse.
        return _Avbrudd(_feil("adresse_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Adresseflatens tilstand i én transaksjon, gjennom tre lesedører."""
    s = conn.execute("SELECT * FROM m19_adressestatus(%s)",
                     (tenant,)).fetchone()
    subjekter = [
        {"subjekt_id": str(r[0]), "ekstern_ref": r[1], "navn": r[2],
         "aktiv": r[3],
         "versjon_id": str(r[4]) if r[4] else None,
         "linje1": r[5], "postnr": r[6], "poststed": r[7], "land": r[8],
         "gjelder_fra": r[9].isoformat() if r[9] else None,
         "kilde": r[10], "siste_metode": r[11], "siste_utfall": r[12],
         "siste_kontrollert": r[13].isoformat() if r[13] else None,
         "versjoner": r[14], "apne_funn": list(r[15] or ())}
        for r in conn.execute("SELECT * FROM m19_subjektene(%s,%s)",
                              (tenant, MAKS_SUBJEKTER)).fetchall()]
    k = conn.execute("SELECT * FROM m19_kravene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "subjekter": s[0], "aktive": s[1], "med_adresse": s[2],
            "kontrollerte": s[3], "apne_funn": s[4],
            "apne_avvist": s[5], "har_krav": s[6],
            "kravversjon": s[7], "vist": len(subjekter)},
        "subjekter": subjekter,
        "krav": None if k is None else {
            "ukontrollert_dogn": k[0], "kontroll_gyldig_dogn": k[1],
            "godkjente_metoder": list(k[2] or ()), "versjon": k[3],
            "oppdatert": k[4].isoformat(), "oppdatert_av": k[5]}}


def adressebilde(tjeneste, request):
    """GET /v1/adresse (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def historikk_endepunkt(tjeneste, request):
    """GET /v1/adresse/{subjekt_id}/historikk (okonomi:read).

    HVER VERSJON, MED BEGGE FORMENE. `endret` sier hvilken linje som var
    et faktisk ADRESSESKIFTE — sammenlignet på den NORMALISERTE formen,
    så to skrivemåter av samme adresse ikke ser ut som en flytting.

    ORIGINALEN ER DET SOM VISES. Normaliseringen er noe vi regner med,
    ikke noe vi presenterer som kundens adresse.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        sid = _sti_uuid(request, "subjekt_id", rid)
        rader = conn.execute(
            "SELECT * FROM m19_adressehistorikken(%s,%s,%s)",
            (auth.tenant, sid, MAKS_HISTORIKK)).fetchall()
        return kanonisk_json({
            "subjekt_id": str(sid),
            "versjoner": [
                {"versjon_id": str(r[0]), "linje1": r[1],
                 "linje2": r[2], "postnr": r[3], "poststed": r[4],
                 "land": r[5], "kilde": r[6], "kilde_ref": r[7],
                 "gjelder_fra": r[8].isoformat(), "notat": r[9],
                 "registrert": r[10].isoformat(), "registrert_av": r[11],
                 "endret": r[12], "kontroller": r[13],
                 "siste_utfall": r[14], "siste_metode": r[15],
                 "siste_kontrollert":
                     r[16].isoformat() if r[16] else None}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def kontroller_endepunkt(tjeneste, request):
    """GET /v1/adresse/versjon/{versjon_id}/kontroller (okonomi:read).

    KONTROLLENE ER NØKLET PÅ VERSJONEN, ikke på subjektet. Endrer kunden
    adresse, er den gamle kontrollen fortsatt sann om den GAMLE
    adressen — og sier ingenting om den nye. Det er nettopp den
    forskjellen `adresse_validert` ville stått og falt på, og derfor er
    den forskjellen synlig i URL-en.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        vid = _sti_uuid(request, "versjon_id", rid)
        rader = conn.execute(
            "SELECT * FROM m19_kontrollene(%s,%s,%s)",
            (auth.tenant, vid, MAKS_KONTROLLER)).fetchall()
        return kanonisk_json({
            "versjon_id": str(vid),
            "kontroller": [
                {"kontroll_id": str(r[0]), "metode": r[1],
                 "utfall": r[2], "kontrollor": r[3], "kilde_ref": r[4],
                 "begrunnelse": r[5],
                 "kontrollert": r[6].isoformat(),
                 "registrert": r[7].isoformat(), "registrert_av": r[8]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen alle fire skriveveiene deler."""
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
        # `felt is None` merker VOID-dørene presist: psycopg gir `''`
        # for VOID, ikke None, så en verdibasert sjekk ville ikke
        # skilt dem fra en dør som lovlig svarer tomt (111s lærdom).
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def krav_endepunkt(tjeneste, request):
    """POST /v1/adresse/krav (bestilling:opprett, idem).

    KRAVET ER TENANTENS. «En visuell sjekk holder» er en
    forretningsbeslutning — en nettbutikk som sender e-bøker og en som
    sender kjøleskap har ikke samme risiko — og en konstant i koden
    ville vært nøyaktig den fullmakten invarianten
    `valideringskrav_hardkodet` forbyr.

    ÆRLIG OM HVA DETTE IKKE ER: kravet går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert og har ingen fasilitet for en
    tenant-innstilling. Invarianten er oppfylt i den forstand som betyr
    noe — tenanten eier og fører verdiene — men koblingen til M-1 står
    igjen som et NAVNGITT gap, samme gap som 111 navnga.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        ukontrollert = _heltall(kropp, "ukontrollert_dogn", rid,
                                *KRAVGRENSER["ukontrollert_dogn"])
        gyldig = _heltall(kropp, "kontroll_gyldig_dogn", rid,
                          *KRAVGRENSER["kontroll_gyldig_dogn"])
        metoder = _metoder(kropp, "godkjente_metoder", rid)
        return ("SELECT m19_sett_krav(%s,%s,%s,%s,%s)",
                (tenant, ukontrollert, gyldig, metoder, bid),
                {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_subjekt_endepunkt(tjeneste, request):
    """POST /v1/adresse/subjekt (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        sid = _utled("subjekt", tenant, nokkel)
        return ("SELECT m19_registrer_subjekt(%s,%s,%s,%s,%s)",
                (tenant, sid, ref, navn, bid),
                {"subjekt_id": str(sid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_adresse_endepunkt(tjeneste, request):
    """POST /v1/adresse/{subjekt_id}/versjon (bestilling:opprett, idem).

    ADRESSEN GÅR INN SLIK DEN BLE OPPGITT. API-et trimmer ytterkantene
    (ellers ville et tilfeldig linjeskift blitt en del av «det kunden
    skrev»), men retter ingenting: ingen forkortelser utvides, ingen
    postnumre slås opp, ingen stavefeil rettes.

    NORMALISERINGEN REGNES I BASEN. Den er ikke et felt kalleren kan
    sende: da kunne den vært hva som helst, og kolonnen ville sluttet å
    bety «det vi faktisk sammenlignet på».

    SVARET SIER OM ADRESSEN FAKTISK ER EN ANNEN — sammenlignet på den
    normaliserte formen. Det er dét svaret som gjør at en gammel
    kontroll ikke stille kan gjelde en ny adresse.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        sid = _sti_uuid(request, "subjekt_id", rid)
        linje1 = _tekst(kropp, "linje1", rid, MAKS_LINJE)
        linje2 = _tekst_valgfri(kropp, "linje2", rid, MAKS_LINJE)
        postnr = _tekst(kropp, "postnr", rid, MAKS_POSTNR)
        poststed = _tekst(kropp, "poststed", rid, MAKS_POSTSTED)
        land = _land(kropp, "land", rid)
        kilde = _valg(kropp, "kilde", rid, KILDER)
        kilde_ref = _tekst(kropp, "kilde_ref", rid, MAKS_REF)
        gjelder_fra = _tekst(kropp, "gjelder_fra", rid, 32)
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        vid = _utled("versjon", tenant, nokkel)
        return ("SELECT m19_registrer_adresse("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, vid, sid, linje1, linje2, postnr, poststed,
                 land, kilde, kilde_ref, gjelder_fra, notat, bid),
                {"subjekt_id": str(sid), "versjon_id": str(vid)},
                "endret")
    return _skriv(tjeneste, request, bygg)


def registrer_kontroll_endepunkt(tjeneste, request):
    """POST /v1/adresse/versjon/{versjon_id}/kontroll (bestilling:opprett).

    HVEM KONTROLLERTE, OG HVORDAN. Begge er påkrevd, og metoden er fra
    et lukket sett der ingen verdi er et oppslag.

    `kontrollor` er ikke det samme som den tekniske aktøren: den som
    faktisk gjorde vurderingen skal stå med navn, også når kallet
    kommer fra en integrasjon.

    BEGRUNNELSEN ER PÅKREVD NÅR UTFALLET IKKE ER `godkjent`. Basen
    håndhever det (112); her fanges det som en 400, fordi det er
    KROPPEN som mangler noe — ikke en tilstand i registeret.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        vid = _sti_uuid(request, "versjon_id", rid)
        metode = _valg(kropp, "metode", rid, METODER)
        utfall = _valg(kropp, "utfall", rid, UTFALL)
        kontrollor = _tekst(kropp, "kontrollor", rid, MAKS_NAVN)
        kilde_ref = _tekst(kropp, "kilde_ref", rid, MAKS_REF)
        begrunnelse = _tekst_valgfri(kropp, "begrunnelse", rid,
                                     MAKS_NOTAT)
        if utfall != "godkjent" and begrunnelse is None:
            raise _Avbrudd(_feil("request_feilformet", rid))
        kontrollert = _tekst(kropp, "kontrollert", rid, 32)
        kid = _utled("kontroll", tenant, nokkel)
        return ("SELECT m19_registrer_kontroll("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s::date,%s)",
                (tenant, kid, vid, metode, utfall, kontrollor,
                 kilde_ref, begrunnelse, kontrollert, bid),
                {"versjon_id": str(vid), "kontroll_id": str(kid)}, None)
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/adresse/{subjekt_id}/aktiv (bestilling:opprett, idem).

    ET SUBJEKT DEAKTIVERES, DET SLETTES ALDRI: et slettet subjekt ville
    tatt adressehistorikken med seg — og den er det eneste som kan
    forklare en feillevering i ettertid.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        sid = _sti_uuid(request, "subjekt_id", rid)
        # `aktiv` ER PÅKREVD HER. `_bool` faller tilbake til `false`, og
        # en kropp uten feltet ville derfor DEAKTIVERT subjektet — altså
        # en utelatelse som utfører en handling (CodeRabbit, 108).
        if "aktiv" not in kropp:
            raise _Avbrudd(_feil("request_feilformet", rid))
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m19_sett_subjektaktiv(%s,%s,%s,%s)",
                (tenant, sid, aktiv, bid),
                {"subjekt_id": str(sid)}, "endret")
    return _skriv(tjeneste, request, bygg)
