"""M-12 identitets- og tilgangsagentens API (migrasjon 097).

Fire endepunkter: én leseflate og tre skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_tilgang_eier`-eid SECURITY DEFINER-dør i 097, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7, 090/091/095/096-formen).
At en tilgang ikke kan registreres uten eier, at hjemmelen ikke kan være
tom, og at en registerrad aldri kan endres etter innsettingen er derfor
egenskaper ved BASEN, ikke ved denne filen — og en flate som sjekket det
selv ville vært en andre sannhet å omgå.

V1-DOMMEN, SOM DET DENNE FILEN IKKE INNEHOLDER: det finnes ingen
identitetsklient her. Ingen import av en M365-, Entra- eller
LDAP-modul, ingen utgående kall, ingen provisjonering. Katalogteksten
lover JML — joiner, mover, leaver — og v1 REGISTRERER i stedet. Det er
invariant nummer én i grensen (`tilgang_endret_utenfor_registeret`), og
den måles statisk på denne filen.

SCOPEVALGET, OG HVORFOR DET IKKE ER `decisions:read`
----------------------------------------------------
Lesingen bærer `security:read`, ikke `decisions:read`. M-21s
pliktregister valgte det motsatte, og forskjellen er innholdet:

  * En pliktliste sier HVA som skal gjøres innen når. Den er tenantens
    egen driftstilstand, og enhver kunderolle har lov til å se den.
  * Et tilgangsregister sier HVEM SOM HAR ADMIN PÅ HVA. Det er et kart
    over angrepsflaten: hvilke systemer som er kritiske, hvilke kontoer
    som er tjenestekontoer, og hvem som eier den enkelte nøkkelen. Med
    `decisions:read` ville hver `leser`, `godkjenner` og
    `policyforvalter` fått det kartet.

`security:read` er scopet `admin` og `sikkerhet` har — compliance/ops —
og det står i `LESESCOPES`, så en browserøkt slipper gjennom
`_autentiser`. Det er samme snitt som `/v1/drift/backup`,
`/v1/datakvalitet`, `/v1/retensjon` og `/v1/modellstyring`, og av samme
grunn: sikkerhetsdata leses av dem som har sikkerhetsinnsyn.

SKRIVEVEIENE GJENBRUKER `bestilling:opprett` (M-21-presedensen).
Registrering av objekt, tilgang og gjennomgang er BESTILLINGER i
plattformens forstand — scopet `admin` allerede har, og som allerede
står i `BROWSER_MUTASJONSSCOPES`. Et nytt scope er en registrering i
autorisasjonslaget med sin egen port, og det skal ikke oppstå av vane.

SP-2 PÅ BEGGE REGISTRERINGENE (m35/096-formen): `objekt_id` og
`tilgang_id` utledes deterministisk av Idempotency-Key-en, så en tapt
respons + nytt klikk GJENSPILLER i stedet for å føde raden en gang til.
Gjennomgangen er idempotent av sin egen dato — døren returnerer samme
frist for en gjennomgang som alt er registrert i dag.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Taket for hvor mange tilganger og funn leseflaten viser. Registerets
#: tak er dørenes (500); dette er flatens, og det er en annen
#: begrunnelse: et register med tusen tilganger skal ikke kunne gjøre ett
#: HTTP-svar til en nedlasting.
MAKS_TILGANGER = 200
MAKS_FUNN = 200

#: Lengdegrensene på kundens egen tekst. `system` og `navn` er navn på
#: ting, `subjekt` er en konto eller en person, og `hjemmel` er en
#: HENVISNING (en rolle, et vedtak, en avtale) — ikke et sammendrag.
#: Alle fire er korte av natur.
MAKS_SYSTEM = 200
MAKS_NAVN = 200
MAKS_SUBJEKT = 320
MAKS_HJEMMEL = 500

#: SPEIL av CHECK-ene i 097. Speilet finnes for at feilen skal bli 400 og
#: ikke 409: en ukjent verdi er en feilformet forespørsel, ikke en
#: tilstand som sier nei. Dørenes CHECK er fortsatt den bindende.
KRITIKALITETER = ("lav", "middels", "hoy", "kritisk")
SUBJEKTTYPER = ("person", "tjenestekonto")
NIVAER = ("les", "skriv", "admin")

#: Gjennomgangsintervallets spenn — speil av CHECKen i 097. Ett døgn er
#: minstemålet; ti år er taket, fordi en «gjennomgang» som kommer
#: sjeldnere enn det ikke er en gjennomgang.
MIN_GJENNOMGANG_DOGN = 1
MAKS_GJENNOMGANG_DOGN = 3650

#: SP-2-navnerommene for de deterministisk utledede id-ene
#: (m8/m35/096-formen). To navnerom, ikke ett: samme Idempotency-Key
#: brukt på objektveien og tilgangsveien skal gi to forskjellige id-er —
#: ellers ville en klient som gjenbrukte nøkkelen fått en UUID-kollisjon
#: mellom to helt ulike rader.
_M12_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m12:tilgang")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M12_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valg(kropp, felt: str, lovlige: tuple[str, ...], rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _uuid_param(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _uuid_felt(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


#: De ENESTE feilklassene dørene bruker som DOM (089/096s liste, samme
#: begrunnelse): en tapt forbindelse eller en manglende rettighet på selve
#: funksjonen er ikke «tilstanden nekter». Å svare 409 på dem ville
#: fortalt et menneske at tilgangen er i feil tilstand, mens sannheten er
#: at basen er nede. Uoversatte feil KASTES VIDERE, så `_med_conn` svarer
#: `db_utilgjengelig` og driftsloggen får dem.
#
#: `InsufficientPrivilege` står bevisst IKKE i listen (096s
#: CodeRabbit-lærdom, og den gjelder like sterkt her): M-12s radvakt
#: feller sine dommer med den ERRCODE-en, men INGEN av dem kan nås gjennom
#: dørene — dørene skriver aldri en frosset kolonne, og gjennomgangsdøren
#: setter `disponit.aktor` selv. En `insufficient_privilege` herfra er
#: derfor alltid det den ser ut som: et manglende grant eller en tapt
#: tenantkontekst. Det er en DRIFTSTILSTAND, og den skal bli 503 med en
#: driftslogg.
_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
)


def _doerfeil(e, rid):
    """Dørens ERRCODE → flatens feilkode. ÉN kilde, så alle tre
    skriveveiene svarer likt på samme dom. `None` = ikke en dom;
    kalleren kaster originalen videre."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        # TO HELT ULIKE SANNHETER MED SAMME SQLSTATE, og de skal ikke
        # svare likt. SP-2s materialitetskonflikt reises av DØREN med en
        # `RAISE ... unique_violation` og bærer INGEN constraint_name:
        # den betyr «samme Idempotency-Key, annet innhold». Skjemaets
        # egne unikhetskrav bærer navnet sitt, og de betyr noe annet:
        # objektet eller tildelingen finnes ALT. En kaller som fikk
        # «idempotenskonflikt» på en helt fersk nøkkel ville lett etter
        # feil i sin egen retry-logikk mens sannheten var at tilgangen
        # sto i registeret fra før.
        if getattr(e.diag, "constraint_name", None) in (
                "tilgang_unik", "tilgangsobjekt_unik"):
            return _Avbrudd(_feil("tilgang_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er på en eier som ikke er medlem, og på en
        # gjennomgang uten navn. Kroppen ER velformet — det er tilstanden
        # eller innholdskravet som sier nei.
        return _Avbrudd(_feil("tilgang_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Fremmednøkkelen mot et objekt som ikke finnes, den ikke-tomme
        # CHECKen på hjemmelen, og unikhetskravet på (objekt, subjekt,
        # nivå) lander her.
        return _Avbrudd(_feil("tilgang_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Hele leseflatens tilstand i én transaksjon, gjennom de to
    lesedørene.

    Rekkefølgen er DØRENES (frist først for tilgangene, frist først for
    funnene) — flaten sorterer ikke om. `dogn_til_gjennomgang` er regnet
    i BASEN, i samme skann som raden, nettopp for at flaten ikke skal
    trekke to datoer fra hverandre.
    """
    tilganger = [
        {"tilgang_id": str(r[0]), "objekt_id": str(r[1]), "system": r[2],
         "objektnavn": r[3], "kritikalitet": r[4], "subjekt": r[5],
         "subjekttype": r[6], "niva": r[7], "eier_bruker_id": r[8],
         "eier_navn": r[9], "hjemmel": r[10], "gjennomgang_dogn": r[11],
         "sist_gjennomgatt": r[12].isoformat() if r[12] is not None else None,
         "sist_gjennomgatt_av": r[13],
         "gjennomgang_frist": r[14].isoformat(),
         "dogn_til_gjennomgang": r[15],
         "opprettet": r[16].isoformat()}
        for r in conn.execute("SELECT * FROM m12_tilgangsbilde(%s,%s)",
                              (tenant, MAKS_TILGANGER)).fetchall()]
    # OBJEKTLISTEN ER MED, og den er ikke en bekvemmelighet: uten den
    # måtte registreringsskjemaet bedt et menneske skrive inn en UUID,
    # og et objekt som nettopp ble registrert — men som ennå ikke har en
    # eneste tilgang — ville vært usynlig i en liste utledet av
    # tilgangene.
    objekter = [
        {"objekt_id": str(r[0]), "system": r[1], "navn": r[2],
         "kritikalitet": r[3], "antall_tilganger": int(r[4]),
         "opprettet": r[5].isoformat()}
        for r in conn.execute("SELECT * FROM m12_objekter(%s,%s)",
                              (tenant, MAKS_TILGANGER)).fetchall()]
    funn = [
        {"tilgang_id": str(r[0]), "funntype": r[1], "subjekt": r[2],
         "system": r[3],
         "frist": r[4].isoformat() if r[4] is not None else None,
         "forst_sett": r[5].isoformat(),
         "sist_sett_sveip": r[6].isoformat(), "alder_s": int(r[7])}
        for r in conn.execute("SELECT * FROM m12_apne_funn(%s,%s)",
                              (tenant, MAKS_FUNN)).fetchall()]
    return {"objekter": objekter, "tilganger": tilganger, "funn": funn}


def tilgangsbilde(tjeneste, request):
    """GET /v1/tilgang (security:read) — tenantens eget tilgangsregister,
    objektene og de åpne funnene, i ett kall."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "security:read", _fn)


def registrer_objekt_endepunkt(tjeneste, request):
    """POST /v1/tilgang/objekt (bestilling:opprett, idem) — registrer et
    tilgangsobjekt.

    Objektet er en EGEN registrering og ikke et felt i tilgangsraden. Et
    register der objektet fødes av en skrivefeil i en tilgangsrad er et
    register der «Microsoft 365» og «Microsft 365» er to systemer, og der
    halvparten av tilgangene til det ene er usynlige for den som spør om
    det andre.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        system = _tekst(kropp, "system", rid, MAKS_SYSTEM)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        kritikalitet = _valg(kropp, "kritikalitet", KRITIKALITETER, rid)
        oid = _utled("objekt", tenant, nokkel)
        try:
            ny = conn.execute(
                "SELECT m12_registrer_objekt(%s,%s,%s,%s,%s,%s)",
                (tenant, oid, system, navn, kritikalitet,
                 _bid)).fetchone()[0]
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"objekt_id": str(oid), "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_tilgang_endepunkt(tjeneste, request):
    """POST /v1/tilgang (bestilling:opprett, idem) — registrer en
    tilgang.

    EIEREN ER PÅKREVD I KROPPEN, ikke utledet av innloggingen. Den som
    fører tilgangen inn i registeret er sjelden den som skal svare for
    at den finnes, og en flate som stille satte innloggeren som eier
    ville gjort «tilganger uten eier» sann på papiret og falsk i
    praksis. Døren avviser en eier som ikke er aktivt medlem av
    tenanten.
    """
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        objekt_id = _uuid_felt(kropp, "objekt_id", rid)
        subjekt = _tekst(kropp, "subjekt", rid, MAKS_SUBJEKT)
        subjekttype = _valg(kropp, "subjekttype", SUBJEKTTYPER, rid)
        niva = _valg(kropp, "niva", NIVAER, rid)
        eier = _tekst(kropp, "eier_bruker_id", rid, 128)
        hjemmel = _tekst(kropp, "hjemmel", rid, MAKS_HJEMMEL)
        dogn = kropp.get("gjennomgang_dogn")
        # `bool` er en `int` i Python, og `True` ville blitt 1 døgn i
        # basen. En sann/usann-verdi i et døgnfelt er en feilformet
        # forespørsel, ikke en gjennomgang hver dag.
        if not isinstance(dogn, int) or isinstance(dogn, bool) \
                or not (MIN_GJENNOMGANG_DOGN <= dogn
                        <= MAKS_GJENNOMGANG_DOGN):
            raise _Avbrudd(_feil("request_feilformet", rid))
        tid = _utled("tilgang", tenant, nokkel)
        try:
            ny = conn.execute(
                "SELECT m12_registrer_tilgang(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "                             %s)",
                (tenant, tid, objekt_id, subjekt, subjekttype, niva, eier,
                 hjemmel, dogn, _bid)).fetchone()[0]
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        # `ny=false` er et STILLE JA (SP-2): samme nøkkel og samme innhold
        # ga samme tilgang. Kalleren får den samme id-en, og ingenting ble
        # skrevet to ganger.
        return _ok({"tilgang_id": str(tid), "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_gjennomgang_endepunkt(tjeneste, request):
    """POST /v1/tilgang/{tilgang_id}/gjennomgang (bestilling:opprett,
    idem).

    «Jeg har sett på denne tilgangen, og den skal fortsatt finnes» —
    attestert av det innloggede mennesket, i dag. `p_gjennomgatt_av` er
    ØKTENS bruker-id og aldri kroppens: en gjennomgang som kunne
    tilskrives en annen enn den som klikket, ville vært en attestasjon
    uten forfatter.

    DATOEN ER BASENS. Det finnes ikke et felt å tilbakedatere en
    gjennomgang med — en gjennomgang som kan tilbakedateres er en frist
    som kan skyves, og da lukker registeret sine egne funn.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _med_conn,
                                   _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        _krev_idem(request, rid)
        tid = _uuid_param(request, "tilgang_id", rid)
        try:
            frist = conn.execute(
                "SELECT m12_registrer_gjennomgang(%s,%s,%s)",
                (tenant, tid, _bid)).fetchone()[0]
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"tilgang_id": str(tid),
                    "neste_frist": frist.isoformat() if frist else None},
                   rid)

    return _med_conn(tjeneste, rid, kjor)
