"""M-17 kundeserviceagentens API (migrasjon 102, PR-A).

Ni endepunkter: tre leseveier og seks skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_kundeservice_eier`-eid SECURITY DEFINER-dør i 102, og runtime
har ingen tabellrettigheter i det hele tatt (SP-7,
090/091/096/100/101-formen).

MODULEN SENDER INGENTING. Det er v1-dommen, og den er en EGENSKAP VED
DENNE FILEN og ikke bare en intensjon: her finnes ingen SMTP-klient,
ingen import av `smtplib`/`email`/`httpx`/`urllib`, ingen mottakeradresse
og ingen utgående kø. Et automatisk svar til en kunde er en uttalelse på
firmaets vegne; M-57 har alt formen — utkastet lagres, et menneske sender
— og den formen gjelder her av samme grunn. Porten `modulen_sendte_svar`
i `test_m17_kundeservice.py` måler fraværet statisk (AST) og
funksjonelt.

PERSONDATA KRYPTERES HER, ALDRI I BASEN (058/088-formen). Emne, kropp og
utkastets tekst går inn i dørene som ferdig ciphertext fra
`db.kryptering`, og ut igjen som ciphertext som dekrypteres her.
Databasen har aldri tenantens DEK i klartekst, og en SQL-injeksjon som
kom seg forbi RLS ville fått bytes den ikke kan åpne.

AVSENDEREN LAGRES SOM HASH, og hashen regnes HER — ikke i basen. Døren
avviser alt som ikke er 64 hex-tegn, så en kaller som sendte adressen
rått ville fått 409, ikke en stille lagring.

DET UAVKLARTE GÅR TIL M-37s KØ. Ikke til en tabell som heter noe annet.
`m17_til_unntakskoe` skriver i `unntak` med en KRYPTERT payload som bare
bærer henvendelsens id, kanalen og saksbehandlerens egen setning — aldri
kundens tekst. En kopi av teksten i køen ville vært det samme
persondatasettet i to lagre med hver sin retensjon.

SCOPENE ER GJENBRUKT, IKKE NYE.

  * LESINGEN bærer `decisions:read`. Kundeservicekøen er tenantens
    alminnelige arbeidsflate — den som svarer kunder skal se den, og det
    er `leser`-rollens klasse. Til forskjell fra M-13s
    avstemmingsregister er dette ikke virksomhetens pengestrøm, og til
    forskjell fra M-34s kontrollregister er det ikke revisjonsmateriale.
  * SELVE INNHOLDET bærer likevel `kundeservice:innhold` — ET NYTT
    SCOPE, og det eneste her. Å se KØEN (hvem spurte, når, hvor gammelt)
    er en annen handling enn å lese hva kunden SKREV, og den andre er
    persondata. Uten skillet ville enhver som kan se en liste også kunne
    lese hver eneste kundetekst. MERK at scopet er den ENESTE gjerdet i
    PR-A: selve lesingen spores ikke (se `innhold_endepunkt` og 102s
    hode for hvorfor), så hvem som HAR lest en kundes tekst er ikke
    kjent. Gapet er navngitt, ikke skjult.
  * SKRIVINGEN bærer `bestilling:opprett` — scopet `admin` allerede har,
    og som allerede står i `BROWSER_MUTASJONSSCOPES`. Samme presedens som
    M-21 (096), M-34 (100) og M-13 (101).

SP-2 PÅ SKRIVEVEIENE SOM FØDER EN RAD: `henvendelse_id` og `utkast_id`
utledes deterministisk av Idempotency-Key-en. En dobbelt registrert
henvendelse ville sett ut som at kunden spurte to ganger — og da svarer
noen to ganger.
"""
from __future__ import annotations

import hashlib
import uuid as uuidlib

import psycopg

#: Taket for hvor mange henvendelser køflaten viser. Dørens tak er 1000;
#: dette er flatens. SAMMENDRAGET TELLER LIKEVEL ALT — det er hele
#: grunnen til at `m17_kostatus` er en egen dør.
MAKS_KOE = 200

#: Lengdegrensene på kundens egen tekst og på våre egne felter.
MAKS_EKSTERN_REF = 300
MAKS_AVSENDER = 400
MAKS_EMNE = 1000
MAKS_KROPP = 100_000
MAKS_UTKAST = 100_000
MAKS_BEGRUNNELSE = 2000
MAKS_KUNNSKAPSREF = 50
MAKS_KUNNSKAPSREF_LENGDE = 200

#: SPEIL av CHECK-ene i 102. Speilene finnes for at feilen skal bli 400 og
#: ikke 409: en ukjent verdi er en feilformet forespørsel, ikke en
#: tilstand som sier nei. Dørenes CHECK er fortsatt den bindende.
KANALER = ("epost", "skjema", "telefon", "chat")
PRIORITETER = ("kritisk", "hoy", "normal", "lav")
TEMAER = ("faktura", "leveranse", "teknisk", "salg", "klage", "annet")
HANDLINGSTYPER = ("svar_kreves", "til_info", "oppgave", "mote",
                  "nyhetsbrev", "mistenkelig")
UTKASTSTATUS = ("forkastet", "brukt_manuelt")
LUKKEUTFALL = ("besvart", "ikke_aktuell")

#: SP-2-navnerommet.
_M17_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m17:kundeservice")

#: AAD-skillet for køens payload. Uten et eget formål ville en
#: køpayload kunnet dekodes som en henvendelses-payload og omvendt —
#: samme dom som `krypter_bytes`' `formaal` (#162).
_AAD_KOE = b"m17:unntakskoe"


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M17_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valg(kropp, felt: str, rid, lovlige: tuple[str, ...]) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _avsenderhash(verdi: str) -> str:
    """Avsenderen som sha256 over den NORMALISERTE adressen.

    Normaliseringen (trim + lowercase) er det som gjør at
    «Kunde@Eksempel.no» og «kunde@eksempel.no » er den samme avsenderen.
    Uten den ville registeret hatt to «kunder» som er én, og
    gjenkjenning på tvers av henvendelser — hele grunnen til at hashen
    finnes — ville sviktet på det vanligste tilfellet.
    """
    return hashlib.sha256(verdi.strip().lower().encode("utf-8")).hexdigest()


#: De ENESTE feilklassene dørene bruker som DOM (089/096/100/101s liste).
_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
)


def _doerfeil(e, rid):
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: «besvart» uten brukt utkast, en lukket
        # henvendelse i køen, et utkast som alt er avgjort, en avsender
        # som ikke er en hash. Kroppen ER velformet — det er
        # innholdskravet basen håndhever som sier nei.
        return _Avbrudd(_feil("henvendelse_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        return _Avbrudd(_feil("henvendelse_ulovlig_tilstand", rid, 409))
    return None


def _dek(conn, tenant):
    from db import kryptering
    return kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)


def _krypter(dek, key_id, tenant, tekst, *, aad=None):
    from db import kryptering
    return kryptering.krypter(dek, {"t": tekst}, tenant, key_id,
                              ekstra_aad=aad)


def svar_for(conn, tenant: str) -> dict:
    """Køflatens tilstand i én transaksjon, gjennom to lesedører.

    INNHOLDET FØLGER IKKE MED. Emne og kropp hentes av en EGEN dør per
    henvendelse, bak sitt eget scope og med sitt eget evidensspor. Et
    listekall som dro med seg hver eneste kundetekst ville gjort ett
    skjermbilde til en full eksport av persondata — og det er en helt
    annen handling enn å se køen.
    """
    s = conn.execute("SELECT * FROM m17_kostatus(%s)", (tenant,)).fetchone()
    koe = [
        {"henvendelse_id": str(r[0]), "kanal": r[1], "ekstern_ref": r[2],
         "mottatt": r[3].isoformat(), "avsender_hash": r[4],
         "alder_dogn": r[5], "prioritet": r[6], "tema": r[7],
         "handlingstype": r[8], "klassifisert_av": r[9],
         "i_unntakskoe": r[10], "antall_utkast": r[11],
         "brukt_utkast": r[12], "apne_funn": list(r[13] or ())}
        for r in conn.execute("SELECT * FROM m17_koen(%s,%s)",
                              (tenant, MAKS_KOE)).fetchall()]
    return {
        "sammendrag": {
            "apne": s[0], "uklassifiserte": s[1], "i_unntakskoe": s[2],
            "kritiske": s[3], "apne_funn": s[4],
            "lukkede_siste_30": s[5],
            # LISTEN ER AVKORTET, OG FLATEN SKAL KUNNE SI DET.
            "vist": len(koe)},
        "koe": koe}


def kobilde(tjeneste, request):
    """GET /v1/kundeservice (decisions:read) — tenantens egen kø."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


def _skriv(tjeneste, request, bygg, *, scope="bestilling:opprett"):
    """Rammen skriveveiene deler: browserkontekst, idempotens, kropp,
    dørkall, commit.

    `bygg` får en åpen forbindelse med tenantkonteksten satt, og
    returnerer `(sql, args, svar)` eller `(sql, args, svar, idfelt)`.
    Krypteringen skjer INNE i `bygg`, fordi den trenger forbindelsen for
    å hente tenantens DEK.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid, scope)
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        sql, args, svar, *rest = bygg(conn, tenant, bid, nokkel, kropp,
                                      rid, request)
        idfelt = rest[0] if rest else None
        try:
            rad = conn.execute(sql, args).fetchone()
            ut = rad[0]
            if idfelt is not None:
                svar = {**svar, idfelt: str(rad[1])}
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
        if isinstance(ut, bool):
            return _ok({**svar, "ny": ut}, rid)
        return _ok({**svar, "unntak_id": int(ut)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def ta_imot_endepunkt(tjeneste, request):
    """POST /v1/kundeservice/henvendelse (bestilling:opprett, idem).

    AVSENDEREN SENDES SOM ADRESSE OG LAGRES SOM HASH. Kalleren skal ikke
    måtte hashe selv — da ville to klienter normalisert ulikt, og den
    samme kunden blitt to. Adressen forlater aldri denne funksjonen.

    DEN VIRKELIGE IDEMPOTENSEN ER `ekstern_ref`. Idempotency-Key-en
    beskytter mot dobbeltklikk; kanalens egen id beskytter mot den samme
    innboksen lest to ganger — og det siste er det som faktisk skjer.
    Derfor kan svarets `henvendelse_id` være en ANNEN enn den nøkkelen
    utledet, og døren returnerer den lagrede.
    """
    def bygg(conn, tenant, bid, nokkel, kropp, rid, _request):
        kanal = _valg(kropp, "kanal", rid, KANALER)
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_EKSTERN_REF)
        avsender = _tekst(kropp, "avsender", rid, MAKS_AVSENDER)
        emne = _tekst(kropp, "emne", rid, MAKS_EMNE)
        tekst = _tekst(kropp, "kropp", rid, MAKS_KROPP)
        mottatt = _tekst(kropp, "mottatt", rid, 64)
        key_id, dek = _dek(conn, tenant)
        e_ct, e_n = _krypter(dek, key_id, tenant, emne)
        k_ct, k_n = _krypter(dek, key_id, tenant, tekst)
        hid = _utled("henvendelse", tenant, nokkel)
        return ("SELECT * FROM m17_ta_imot(%s,%s,%s,%s,%s::timestamptz,"
                "                          %s,%s,%s,%s,%s,%s,%s)",
                (tenant, hid, kanal, ref, mottatt,
                 _avsenderhash(avsender), e_ct, e_n, k_ct, k_n, key_id,
                 bid),
                {"henvendelse_id": str(hid)}, "henvendelse_id")
    return _skriv(tjeneste, request, bygg)


def klassifiser_endepunkt(tjeneste, request):
    """POST /v1/kundeservice/henvendelse/{henvendelse_id}/klassifiser
    (bestilling:opprett, idem).

    `kilde` er ALLTID `menneske` i PR-A, og det er ikke en parameter
    kalleren velger: en klient som kunne sende `modell` uten en digest
    ville skrevet en modelldom ingen kan spore tilbake til en modell.
    PR-B legger til den veien sammen med digesten.
    """
    def bygg(_conn, tenant, bid, _nokkel, kropp, rid, request):
        hid = _sti_uuid(request, "henvendelse_id", rid)
        prioritet = _valg(kropp, "prioritet", rid, PRIORITETER)
        tema = _valg(kropp, "tema", rid, TEMAER)
        handling = _valg(kropp, "handlingstype", rid, HANDLINGSTYPER)
        return ("SELECT m17_klassifiser(%s,%s,%s,%s,%s,'menneske',NULL,%s)",
                (tenant, hid, prioritet, tema, handling, bid),
                {"henvendelse_id": str(hid)})
    return _skriv(tjeneste, request, bygg)


def unntakskoe_endepunkt(tjeneste, request):
    """POST /v1/kundeservice/henvendelse/{henvendelse_id}/unntakskoe
    (bestilling:opprett, idem).

    DET UAVKLARTE GÅR TIL M-37s KØ, og payloaden bærer bare
    henvendelsens id, kanalen og saksbehandlerens egen setning — kryptert
    med tenantens DEK og med sitt eget AAD-formål. Kundens tekst blir
    IKKE med: den ligger i `henvendelse`, og en kopi i køen ville vært
    det samme persondatasettet i to lagre med hver sin retensjon.
    """
    def bygg(conn, tenant, bid, _nokkel, kropp, rid, request):
        hid = _sti_uuid(request, "henvendelse_id", rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        key_id, dek = _dek(conn, tenant)
        from db import kryptering
        ct, nonce = kryptering.krypter(
            dek, {"modul": "m17_kundeservice",
                  "henvendelse_id": str(hid),
                  "begrunnelse": begrunnelse},
            tenant, key_id, ekstra_aad=_AAD_KOE)
        return ("SELECT m17_til_unntakskoe(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, hid, begrunnelse, ct, nonce, key_id, bid),
                {"henvendelse_id": str(hid)})
    return _skriv(tjeneste, request, bygg)


def utkast_endepunkt(tjeneste, request):
    """POST /v1/kundeservice/henvendelse/{henvendelse_id}/utkast/ny
    (bestilling:opprett, idem).

    APPEND-ONLY: hver regenerering er en NY rad. Et utkast som endres
    under føttene på den som leser det, er et utkast ingen kan stå for å
    ha sendt.
    """
    def bygg(conn, tenant, bid, nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        hid = _sti_uuid(request, "henvendelse_id", rid)
        tekst = _tekst(kropp, "tekst", rid, MAKS_UTKAST)
        refs = kropp.get("kunnskapsref") or []
        if not isinstance(refs, list) or len(refs) > MAKS_KUNNSKAPSREF:
            raise _Avbrudd(_feil("request_feilformet", rid))
        for r in refs:
            if not isinstance(r, str) or not r.strip() \
                    or len(r) > MAKS_KUNNSKAPSREF_LENGDE:
                raise _Avbrudd(_feil("request_feilformet", rid))
        key_id, dek = _dek(conn, tenant)
        ct, nonce = _krypter(dek, key_id, tenant, tekst)
        uid = _utled("utkast", tenant, nokkel)
        return ("SELECT m17_lagre_utkast(%s,%s,%s,%s,%s,%s,%s,'menneske',"
                "                        NULL,%s)",
                (tenant, uid, hid, ct, nonce, key_id, list(refs), bid),
                {"henvendelse_id": str(hid), "utkast_id": str(uid)})
    return _skriv(tjeneste, request, bygg)


def utkastdom_endepunkt(tjeneste, request):
    """POST /v1/kundeservice/utkast/{utkast_id}/dom (bestilling:opprett).

    `brukt_manuelt` er sporet etter at ET MENNESKE sendte noe basert på
    utkastet — aldri at modulen sendte det. Det finnes ingen verdi som
    heter `sendt`, og fraværet er dommen.
    """
    def bygg(_conn, tenant, bid, _nokkel, kropp, rid, request):
        uid = _sti_uuid(request, "utkast_id", rid)
        status = _valg(kropp, "status", rid, UTKASTSTATUS)
        return ("SELECT m17_avgjor_utkast(%s,%s,%s,%s)",
                (tenant, uid, status, bid),
                {"utkast_id": str(uid), "status": status})
    return _skriv(tjeneste, request, bygg)


def lukk_endepunkt(tjeneste, request):
    """POST /v1/kundeservice/henvendelse/{henvendelse_id}/lukk
    (bestilling:opprett, idem).

    «Besvart» krever et utkast merket `brukt_manuelt`. Kravet håndheves
    av vakten OG av døren i 102; her sjekkes det ikke — et forsøk blir
    409 fordi BASEN nektet, ikke fordi API-et sjekket.
    """
    def bygg(_conn, tenant, bid, _nokkel, kropp, rid, request):
        hid = _sti_uuid(request, "henvendelse_id", rid)
        utfall = _valg(kropp, "utfall", rid, LUKKEUTFALL)
        return ("SELECT m17_lukk(%s,%s,%s,%s)",
                (tenant, hid, utfall, bid),
                {"henvendelse_id": str(hid), "utfall": utfall})
    return _skriv(tjeneste, request, bygg)


def innhold_endepunkt(tjeneste, request):
    """GET /v1/kundeservice/henvendelse/{henvendelse_id}/innhold
    (kundeservice:innhold).

    EGET SCOPE. Å se køen er én handling; å lese hva kunden skrev er en
    annen, og den andre er persondata.

    ET DOKUMENTERT GAP: LESINGEN SPORES IKKE. Registeret vet i dag hvem
    som SVARTE, ikke hvem som LESTE. Grunnen står i 102s hode og gjentas
    kort her: husets lesevei ruller alltid tilbake, med en uttalt
    begrunnelse, og en GET som committet ville undergravd nettopp den
    invarianten. Gapet lukkes når huset får en registrert lesevei — det
    er ikke en avgjørelse denne PR-en skal ta alene.

    DEKRYPTERINGEN SKJER HER. Basen har aldri tenantens DEK i klartekst.
    """
    from .app import _rid
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        hid = _sti_uuid(request, "henvendelse_id", rid)
        try:
            rad = conn.execute("SELECT * FROM m17_hent_innhold(%s,%s)",
                               (auth.tenant, hid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        if rad is None:
            # Skal ikke kunne skje — døren RAISEr på ukjent henvendelse,
            # og RLS-predikatet er det samme i begge setningene. Vakten
            # står likevel: et `rad[4]` på None ville gitt 500 der 404 er
            # det ærlige svaret, og «skal ikke kunne skje» er ingen
            # feilhåndtering.
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("ikke_funnet", rid, 404))
        from db import kryptering
        dek = kryptering.hent_dek(conn, auth.tenant, rad[4])
        svar = {
            "henvendelse_id": str(hid),
            "emne": kryptering.dekrypter(dek, rad[0], rad[1],
                                         auth.tenant, rad[4])["t"],
            "kropp": kryptering.dekrypter(dek, rad[2], rad[3],
                                          auth.tenant, rad[4])["t"],
            "request_id": rid}
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "kundeservice:innhold", _fn)


def utkastene_endepunkt(tjeneste, request):
    """GET /v1/kundeservice/henvendelse/{henvendelse_id}/utkast
    (kundeservice:innhold).

    Utkastene er FORSLAG TIL SVAR til den samme kunden, altså samme
    klasse som henvendelsens innhold — og de står derfor bak det samme
    scopet. Et utkast lest av noen som ikke skal se henvendelsen er den
    samme lekkasjen med et annet navn.
    """
    from .app import _rid
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        hid = _sti_uuid(request, "henvendelse_id", rid)
        rader = conn.execute("SELECT * FROM m17_utkastene(%s,%s)",
                             (auth.tenant, hid)).fetchall()
        from db import kryptering
        ut = []
        for r in rader:
            dek = kryptering.hent_dek(conn, auth.tenant, r[3])
            ut.append({
                "utkast_id": str(r[0]),
                "tekst": kryptering.dekrypter(dek, r[1], r[2],
                                              auth.tenant, r[3])["t"],
                "kunnskapsref": list(r[4] or ()), "kilde": r[5],
                "modell_digest": r[6], "status": r[7],
                "opprettet": r[8].isoformat(), "opprettet_av": r[9]})
        return kanonisk_json({"henvendelse_id": str(hid), "utkast": ut,
                              "request_id": rid},
                             200, {"x-request-id": rid})
    return _les(tjeneste, request, "kundeservice:innhold", _fn)
