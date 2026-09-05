"""M-47 myndighetsrapporteringsagentens API (migrasjon 123).

Tretten endepunkter: fem leseveier og åtte skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_myndighet_eier`-eid SECURITY DEFINER-dør i 123, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM SENDER INN. En innsending til en myndighet er
BINDENDE og kan ikke kalles tilbake. 123 har ingen mottaker, ingen
utboks og ingen signatur, og det er ikke en sjekk her — det er et
fravær som er målt av en port.

MEN HER ER FRAVÆRET IKKE NOK, OG DET SKILLER M-47 FRA KLYNGE 6.

For de fem der var skaden å HANDLE. Her er skaden også å LA VÆRE: en
frist som går uten innsending er nøyaktig det modulen ble bygget for å
hindre. EN STILLE M-47 ER VERRE ENN INGEN M-47.

FIRE NEKT SOM ER VERDT Å KJENNE:

  * `POST /plikt` NEKTER uten tenantens varselfrist. Uten den finnes
    det ingen frist å varsle på, og plikten ville ligget i registeret
    og SETT overvåket ut mens ingenting så etter den.

  * `POST /plikt` NEKTER mot et avviklet regelverk. Regelverket kan
    REGISTRERES avviklet — arkivet skal kunne svare på hva regelen sa
    DA — men en NY plikt mot det ville hvilt på en hjemmel som ikke
    gjelder.

  * `POST /bevis` NEKTER en dato i framtiden. Et bevis datert i morgen
    er ikke et bevis, det er en plan, og en plan lukker ikke et
    fristfunn.

  * `POST /funn/{id}/lukk` NEKTER på to funntyper.
    `frist_passert_uten_bevis` og `plikt_mot_utlopt_regelverk` lukkes
    av at TILSTANDEN er borte, ikke av at noen trykket. Regelen bor i
    basen (`m47_funn_er_sveipens`), og lesedøra gir `kan_lukkes` med
    hver rad så flaten slipper å kopiere den.

BEVISET ER IKKE «SENDT»-KOLONNEN MED ET ANNET NAVN. `POST /bevis`
registrerer at et MENNESKE har sendt inn, et annet sted, og bærer
kvitteringsreferansen myndigheten ga DEM. Vi har ingen kanal til
myndigheten og påstår ikke å ha det. Kolonnen heter
`innsendt_av_person` av nøyaktig den grunnen.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett` — samme
presedens som 096/100–123.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en, så
en gjentatt POST ikke blir to plikter med samme frist.
"""
from __future__ import annotations

import datetime
import re
import uuid as uuidlib

import psycopg

MAKS_REGELVERK = 200
MAKS_PLIKTTYPER = 200
MAKS_PLIKTER = 500
MAKS_TEKST = 4000
MAKS_NAVN = 500
MAKS_URL = 2000
MAKS_REF = 200

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 123.
KRAVGRENSER = {
    "varselfrist_dogn": (1, 365),
    "eskaleringsfrist_dogn": (1, 90),
    "regelvarsel_dogn": (1, 730),
}

MYNDIGHETER = ("skatteetaten", "altinn", "brreg", "ssb", "nav",
               "arbeidstilsynet", "annen")
FREKVENSER = ("maanedlig", "to_maanedlig", "kvartalsvis",
              "halvaarlig", "aarlig", "ved_hendelse")

_M47_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL,
                        "disponit:m47:myndighetsrapport")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M47_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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


def _dato_valgfri(kropp, felt: str, rid) -> str | None:
    """Ved OPPRETTELSE er utelatt nøkkel og eksplisitt `null` det
    samme, og det er riktig: et regelverk uten avviklingsdato gjelder
    fortsatt."""
    if kropp.get(felt) is None:
        return None
    return _dato(kropp, felt, rid)


def _dato_som_kan_vaere_null(kropp, felt: str, rid) -> str | None:
    """NØKKELEN MÅ STÅ, MEN VERDIEN KAN VÆRE `null` (121s lærdom).

    På `/gyldig-til` er forskjellen mellom «utelatt» og «eksplisitt
    null» hele saken: en klient som GLEMMER feltet ville ellers
    stilltiende gjort «avvikles 31. desember» om til «gjelder
    fortsatt» — nøyaktig feilen modulen finnes for å hindre.
    """
    from .policyadmin_http import _Avbrudd, _feil
    if felt not in kropp:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if kropp[felt] is None:
        return None
    return _dato(kropp, felt, rid)


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


#: Maskinnøkkelen, ORDRETT som CHECK-en i 123.
#:
#: `str.isalpha()` var GALT her (CodeRabbit): den er unicode-bevisst, så
#: «æbc» og «ßx1» passerte API-et og traff først databasens ASCII-CHECK
#: — altså en 500 der brukeren skulle fått en 400 med en forklaring.
#: Mønsteret står ett sted og speiler basens, tegn for tegn.
_NOKKEL_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _nokkel(kropp, felt: str, rid) -> str:
    """Maskinnøkkelen til en plikttype. Speiler CHECK-en i 123."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, 64).lower()
    if not _NOKKEL_RE.match(verdi):
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
    """Dørenes dommer → API-feil. Samme form som 112–123."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if ("rapportplikt_unik" in str(e)
                or "regelverk_unik" in str(e)
                or "plikttype_unik" in str(e)
                or "rapportbevis_unik" in str(e)):
            return _Avbrudd(_feil("myndighet_ulovlig_tilstand", rid,
                                  409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: plikt uten krav, plikt mot avviklet
        # regelverk, bevis i framtiden, og de to funnene sveipen eier.
        return _Avbrudd(_feil("myndighet_ulovlig_tilstand", rid, 409))
    return None


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def _regelverksrad(r) -> dict:
    return {
        "regelverk_id": str(r[0]), "myndighet": r[1], "navn": r[2],
        "versjon": r[3],
        # HJEMMELEN FØLGER RADEN. En frist uten hjemmel er en påstand om
        # at noen må gjøre noe, uten å si hvem som har bestemt det.
        "hjemmel": r[4],
        "gyldig_fra": r[5].isoformat(),
        "gyldig_til": r[6].isoformat() if r[6] else None,
        # GYLDIGHETEN REGNES I BASEN. To lesere skal ikke kunne komme
        # til hver sin konklusjon om hvorvidt hjemmelen fortsatt står.
        "gyldig_naa": r[7], "dogn_til_utlop": r[8],
        "innhold_sha256": r[9], "kilde_url": r[10],
        "antall_plikter": r[11],
    }


def _pliktrad(r) -> dict:
    return {
        "plikt_id": str(r[0]), "plikttype_id": str(r[1]),
        "typenavn": r[2], "typenokkel": r[3],
        "periode_fra": r[4].isoformat(),
        "periode_til": r[5].isoformat(),
        "frist": r[6].isoformat(),
        # NEGATIVT TALL BETYR PASSERT. Fortegnet er hele beskjeden, og
        # en flate som bare fikk «antall døgn» ville ikke visst hvilken
        # vei det gikk.
        "dogn_til_frist": r[7],
        "myndighet": r[8], "regelnavn": r[9], "regelversjon": r[10],
        "hjemmel": r[11], "regelverk_gyldig_naa": r[12],
        # BEVISET, ELLER FRAVÆRET AV DET. `null` her er ikke tomt — det
        # er modulens viktigste opplysning: ingen har sendt inn ennå.
        "bevis_id": str(r[13]) if r[13] else None,
        "innsendt_dato": r[14].isoformat() if r[14] else None,
        "kvittering_ref": r[15],
        "innsendt_av_person": r[16],
        "dogn_etter_frist": r[17],
        "kravversjon": r[18], "registrert": r[19].isoformat(),
        "registrert_av": r[20],
    }


def svar_for(conn, tenant: str) -> dict:
    """Myndighetsflatens tilstand i én transaksjon, gjennom fire dører."""
    s = conn.execute("SELECT * FROM m47_bildet(%s,%s)",
                     (tenant, MAKS_PLIKTER)).fetchone()
    regelverk = [_regelverksrad(r) for r in conn.execute(
        "SELECT * FROM m47_regelverkene(%s,%s)",
        (tenant, MAKS_REGELVERK)).fetchall()]
    plikttyper = [
        {"plikttype_id": str(r[0]), "nokkel": r[1], "navn": r[2],
         "frekvens": r[3], "beskrivelse": r[4],
         "antall_plikter": r[5]}
        for r in conn.execute("SELECT * FROM m47_plikttypene(%s,%s)",
                              (tenant, MAKS_PLIKTTYPER)).fetchall()]
    plikter = [_pliktrad(r) for r in conn.execute(
        "SELECT * FROM m47_pliktene(%s,%s)",
        (tenant, MAKS_PLIKTER)).fetchall()]
    return {
        "sammendrag": {
            "plikter": s[0], "beviste": s[1], "ubeviste": s[2],
            # DET ENE TALLET MODULEN FINNES FOR: frister som har gått
            # uten at noen sendte inn.
            "frist_passert": s[3], "frist_naer": s[4],
            "regelverk": s[5], "gyldige": s[6], "utlopte": s[7],
            "apne_funn": s[8], "har_krav": s[9],
            "varselfrist_dogn": s[10], "kravversjon": s[13],
            "vist": s[14],
        },
        # ALLE TRE TERSKLENE (CodeRabbit). Skjemaet forhåndsfyller seg
        # herfra: med bare varselfristen sto de to andre feltene tomme,
        # og en tenant som lagret ville sendt 0 inn i felt med minimum
        # 1. Et skjema som viser mindre enn det lagrer er en felle.
        "krav": ({"varselfrist_dogn": s[10],
                  "eskaleringsfrist_dogn": s[11],
                  "regelvarsel_dogn": s[12],
                  "versjon": s[13]}
                 if s[9] else None),
        "regelverk": regelverk,
        "plikttyper": plikttyper,
        "plikter": plikter,
    }


def myndighetsbilde(tjeneste, request):
    """GET /v1/myndighet (okonomi:read) — tenantens eget pliktregister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def regelverk_endepunkt(tjeneste, request):
    """GET /v1/myndighet/regelverk (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m47_regelverkene(%s,%s)",
                             (auth.tenant, MAKS_REGELVERK)).fetchall()
        # AVKORTINGEN SIES (122s CodeRabbit-funn). En stille avkortet
        # liste er verre enn en tom: den som ikke finner hjemmelen sin
        # tror den ikke finnes, i stedet for å vite at han ikke har
        # sett alle.
        svar = {"request_id": rid, "vist": len(rader),
                "grense": MAKS_REGELVERK,
                "regelverk": [_regelverksrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def plikter_endepunkt(tjeneste, request):
    """GET /v1/myndighet/plikter (okonomi:read).

    DEN NÆRMESTE FRISTEN FØRST, og de passerte aller først. Sorteringen
    skjer i basen: en liste sortert på registreringstidspunkt ville
    begravd avviket under alt som er i orden.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m47_pliktene(%s,%s)",
                             (auth.tenant, MAKS_PLIKTER)).fetchall()
        svar = {"request_id": rid, "vist": len(rader),
                "grense": MAKS_PLIKTER,
                "plikter": [_pliktrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/myndighet/funn (okonomi:read).

    `kan_lukkes` KOMMER FRA BASEN, ikke fra en kopi i klienten. To av
    funntypene lukkes bare av sveipen, og regelen bor ÉTT sted
    (`m47_funn_er_sveipens`) — en lukkeknapp som alltid feiler er verre
    enn en valgmulighet som ikke finnes.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m47_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "funn": [
            {"funn_id": str(r[0]), "funntype": r[1],
             "regelverk_id": str(r[2]) if r[2] else None,
             "plikt_id": str(r[3]) if r[3] else None,
             "myndighet": r[4], "regelnavn": r[5],
             "regelversjon": r[6], "typenavn": r[7],
             "frist": r[8].isoformat() if r[8] else None,
             "over_grense": r[9], "detalj": r[10],
             "kravversjon": r[11], "kan_lukkes": r[12],
             "forst_sett": r[13].isoformat(),
             "sist_sett_sveip": r[14].isoformat(), "apen": r[15],
             "lukket_ts": r[16].isoformat() if r[16] else None,
             "lukket_av": r[17], "lukkenotat": r[18]}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen sju av de åtte skriveveiene deler.

    `/plikt` og `/bevis` bruker den IKKE: de returnerer en RAD, ikke en
    skalar. Den som registrerer en plikt skal se hjemmelen, versjonen og
    hvor mange døgn det er til fristen — og den som registrerer et bevis
    skal se om det kom for sent.
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
    """POST /v1/myndighet/krav (bestilling:opprett, idem).

    VARSELFRISTEN ER TENANTENS. En bedrift med regnskapsfører og fjorten
    dagers internfrist trenger et annet varsel enn en som gjør det selv
    kvelden før. En konstant her ville vært en fullmakt modulen ga seg
    selv over kundens forsinkelsesgebyr.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        varsel = _heltall(kropp, "varselfrist_dogn", rid,
                          *KRAVGRENSER["varselfrist_dogn"])
        esk = _heltall(kropp, "eskaleringsfrist_dogn", rid,
                       *KRAVGRENSER["eskaleringsfrist_dogn"])
        regel = _heltall(kropp, "regelvarsel_dogn", rid,
                         *KRAVGRENSER["regelvarsel_dogn"])
        return ("SELECT versjon FROM m47_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, varsel, esk, regel, bid, nokkel), {},
                "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_regelverk_endepunkt(tjeneste, request):
    """POST /v1/myndighet/regelverk (bestilling:opprett, idem).

    ET ALT AVVIKLET REGELVERK KAN REGISTRERES, og det er med vilje: en
    plikt fra 2019 må kunne forstås mot hjemmelen som gjaldt DA.
    REGISTRERING ER ARKIVERING. Skillet går ved PLIKTEN — `/plikt`
    nekter mot et regelverk som ikke gjelder i dag (121s lærdom, som
    var min egen feil der).
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        myndighet = _valg(kropp, "myndighet", rid, MYNDIGHETER)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        versjon = _tekst(kropp, "versjon", rid, MAKS_NAVN)
        hjemmel = _tekst(kropp, "hjemmel", rid, MAKS_TEKST)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        sha = _sha256(kropp, "innhold_sha256", rid)
        url = _tekst_valgfri(kropp, "kilde_url", rid, MAKS_URL)
        rid_uuid = _utled("regelverk", tenant, nokkel)
        return ("SELECT gyldig_naa FROM m47_registrer_regelverk("
                "%s,%s,%s,%s,%s,%s,%s,%s::date,%s,%s,%s)",
                (tenant, rid_uuid, myndighet, navn, versjon, hjemmel,
                 fra, til, sha, url, bid),
                {"regelverk_id": str(rid_uuid)}, "gyldig_naa")
    return _skriv(tjeneste, request, bygg)


def sett_gyldig_til_endepunkt(tjeneste, request):
    """POST /v1/myndighet/regelverk/{regelverk_id}/gyldig-til.

    NØKKELEN MÅ STÅ (121s lærdom). En klient som GLEMMER feltet ville
    ellers stilltiende gjort «avvikles 31. desember» om til «gjelder
    fortsatt» — nøyaktig feilen modulen finnes for å hindre.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        rvid = _sti_uuid(request, "regelverk_id", rid)
        til = _dato_som_kan_vaere_null(kropp, "gyldig_til", rid)
        return ("SELECT gyldig_naa FROM m47_sett_gyldig_til("
                "%s,%s,%s::date,%s)",
                (tenant, rvid, til, bid),
                {"regelverk_id": str(rvid), "gyldig_til": til},
                "gyldig_naa")
    return _skriv(tjeneste, request, bygg)


def registrer_plikttype_endepunkt(tjeneste, request):
    """POST /v1/myndighet/plikttype (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        maskinnokkel = _nokkel(kropp, "nokkel", rid)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        frekvens = _valg(kropp, "frekvens", rid, FREKVENSER)
        beskrivelse = _tekst_valgfri(kropp, "beskrivelse", rid,
                                     MAKS_TEKST)
        tid = _utled("plikttype", tenant, nokkel)
        return ("SELECT nokkel FROM m47_registrer_plikttype("
                "%s,%s,%s,%s,%s,%s,%s)",
                (tenant, tid, maskinnokkel, navn, frekvens,
                 beskrivelse, bid),
                {"plikttype_id": str(tid)}, "nokkel")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/myndighet/funn/{funn_id}/lukk (bestilling:opprett).

    TO FUNNTYPER NEKTES AV DØRA, og det er ikke symmetri med de andre
    modulene — det er modulens dom. `frist_passert_uten_bevis` er ikke
    en mening man kan være uenig i: fristen HAR gått. Å klikke den bort
    ville vært å skru av det ene varselet som sier at noe faktisk har
    gått galt, og forsinkelsesgebyret kommer uansett.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "funn_id", rid)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT apen FROM m47_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, notat, bid),
                {"funn_id": str(fid)}, "apen")
    return _skriv(tjeneste, request, bygg)


def registrer_plikt_endepunkt(tjeneste, request):
    """POST /v1/myndighet/plikt (bestilling:opprett, idem).

    EGEN RAMME, fordi svaret er en RAD. Den som registrerer en plikt
    skal se HJEMMELEN den hviler på, hvilken regelversjon den ble
    registrert mot, og hvor mange døgn det er til fristen — ikke bare
    at det gikk bra.

    DØRA NEKTER uten tenantens varselfrist, og mot et regelverk som
    ikke gjelder i dag. Begge nektene er modulens dom, ikke API-ets
    sjekk: de står i 123, og porten måler dem der.
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
        tid = _kropp_uuid(kropp, "plikttype_id", rid)
        rvid = _kropp_uuid(kropp, "regelverk_id", rid)
        fra = _dato(kropp, "periode_fra", rid)
        til = _dato(kropp, "periode_til", rid)
        frist = _dato(kropp, "frist", rid)
        pid = _utled("plikt", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m47_registrer_plikt("
                "%s,%s,%s,%s,%s::date,%s::date,%s::date,%s)",
                (tenant, pid, tid, rvid, fra, til, frist,
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
        return _ok({"plikt_id": str(pid),
                    "frist": rad[1].isoformat(),
                    "dogn_til_frist": rad[2],
                    "myndighet": rad[3], "regelnavn": rad[4],
                    "regelversjon": rad[5], "hjemmel": rad[6],
                    "kravversjon": rad[7]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_bevis_endepunkt(tjeneste, request):
    """POST /v1/myndighet/plikt/{plikt_id}/bevis.

    DETTE ER IKKE «SEND»-RUTEN MED ET ANNET NAVN. Den registrerer at et
    MENNESKE har sendt inn, et annet sted, og bærer kvitteringsreferansen
    myndigheten ga DEM. Vi har ingen kanal til myndigheten.

    EGEN RAMME, fordi svaret er en RAD: den som registrerer skal se om
    beviset kom FOR SENT. Et bevis registrert etter fristen er fortsatt
    et bevis — men at det kom for sent er en opplysning noen skal kunne
    finne igjen, og den står både her og i evidenskjeden.

    DØRA NEKTER EN FRAMTIDSDATO. Et bevis datert i morgen er ikke et
    bevis, det er en plan, og en plan lukker ikke et fristfunn.
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
        pid = _sti_uuid(request, "plikt_id", rid)
        dato = _dato(kropp, "innsendt_dato", rid)
        kvittering = _tekst(kropp, "kvittering_ref", rid, MAKS_REF)
        person = _tekst(kropp, "innsendt_av_person", rid, MAKS_NAVN)
        notat = _tekst_valgfri(kropp, "notat", rid, MAKS_TEKST)
        bevis_id = _utled("bevis", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m47_registrer_bevis("
                "%s,%s,%s,%s::date,%s,%s,%s,%s)",
                (tenant, bevis_id, pid, dato, kvittering, person,
                 notat, bid)).fetchone()
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
        return _ok({"bevis_id": str(bevis_id),
                    "plikt_id": str(pid),
                    "innsendt_dato": rad[2].isoformat(),
                    "frist": rad[3].isoformat(),
                    # POSITIVT TALL BETYR FOR SENT.
                    "dogn_etter_frist": rad[4]}, rid)

    return _med_conn(tjeneste, rid, kjor)
