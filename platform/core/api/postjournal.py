"""M-50 postjournal- og innsynsvaktens API (migrasjon 124).

Fjorten endepunkter: seks leseveier og åtte skriveveier, alle mot
dører. Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_postjournal_eier`-eid SECURITY DEFINER-dør i 124, og runtime
har ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM HENTER, og ingen som sender en henvendelse.

DEN NÆRLIGGENDE BEGRUNNELSEN TREFFER IKKE: postjournaler ER
offentlige. Det som treffer er at de inneholder NAVNGITTE
PRIVATPERSONER, og at en systematisk høsting er en helt annen
behandling enn det enkeltoppslag et menneske gjør. Ett oppslag er
innsyn; ti tusen oppslag sammenstilt i et register er en profil — og
profilen er VÅR, ikke kommunens.

`hentet_av_person` heter det den er. Det finnes ingen
`hentet_automatisk`, og det er ikke en forglemmelse.

FEM NEKT SOM ER VERDT Å KJENNE:

  * `POST /post` NEKTER uten tenantens oppbevaringsgrenser. Uten dem
    finnes det ingen maksimal oppbevaringstid å måle slettefristen
    mot.

  * `POST /post` NEKTER mot en avviklet kildeversjon. Arkivet tar imot
    den; en NY post lest i et format som er lagt om ville vært en
    registrering der feltene kan bety noe annet enn de gjorde.

  * `POST /post` NEKTER en slettefrist utover tenantens tak. En frist
    på ti år i et register med ett års tak er ikke en plan — det er en
    omgåelse av planen.

  * `POST /post` KREVER personene i SAMME kall. Døra skriver posten og
    personene i én setning: en journalpost med navngitte
    privatpersoner kan ikke eksistere uten slettefrister, heller ikke
    i et vindu mellom to kall.

  * `POST /funn/{id}/lukk` NEKTER på to funntyper.
    `slettefrist_passert` og `post_mot_utlopt_kilde` lukkes av at
    TILSTANDEN er borte. Regelen bor i basen
    (`m50_funn_er_sveipens`), og lesedøra gir `kan_lukkes` med hver
    rad så flaten slipper å kopiere den.

ANONYMISERING, IKKE SLETTING. `POST /person/{id}/anonymiser` tømmer
navnet og setter et spor. At vi HAR oppbevart noen skal fortsatt kunne
leses av den som spør — men uten navnet. Sletting ville fjernet
beviset på at vi hadde den.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett` — samme
presedens som 096/100–124.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import datetime
import re
import uuid as uuidlib

import psycopg

MAKS_KILDER = 200
MAKS_SAKER = 200
MAKS_POSTER = 500
MAKS_PERSONER = 50
MAKS_TEKST = 4000
MAKS_NAVN = 500
MAKS_URL = 2000
MAKS_REF = 200
#: Formålet må være SKREVET, ikke huket av. Seksten tegn er ikke en
#: kvalitetsgaranti — det er en terskel mot «salg» og «marked» som
#: eneste begrunnelse for å samle navngitte privatpersoner.
MIN_FORMAAL = 16

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 124.
KRAVGRENSER = {
    "sletteplan_maks_dogn": (1, 3650),
    "slettevarsel_dogn": (1, 365),
    "kildevarsel_dogn": (1, 730),
}

FORMATER = ("noark5", "einnsyn", "kommunal_web", "annet")
GRUNNLAG = ("berettiget_interesse", "avtale", "rettslig_forpliktelse",
            "samtykke")
ROLLER = ("avsender", "mottaker", "part", "omtalt")

_M50_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL,
                        "disponit:m50:postjournal")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M50_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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




#: Organisasjonsnummeret, ORDRETT som CHECK-en i 124.
#:
#: `_tekst_valgfri(..., 9)` var GALT (CodeRabbit): den måler LENGDE, så
#: «abcdefghi» passerte API-et og traff først databasens sifferkrav —
#: altså en 500 der brukeren skulle fått en 400 med en forklaring.
#: Samme klasse som `str.isalpha()` i M-47 (123), og fanget av samme
#: grunn: mønsteret må stå ett sted og speile basens.
_ORGNR_RE = re.compile(r"^[0-9]{9}$")


def _organnummer(kropp, felt: str, rid) -> str | None:
    from .policyadmin_http import _Avbrudd, _feil
    if kropp.get(felt) is None:
        return None
    verdi = _tekst(kropp, felt, rid, 9)
    if not _ORGNR_RE.match(verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _formaal(kropp, felt: str, rid) -> str:
    """FORMÅLET MÅ VÆRE SKREVET, IKKE HUKET AV.

    Seksten tegn er ikke en kvalitetsgaranti. Det er en terskel mot at
    «salg» eller «marked» blir stående alene som begrunnelsen for å
    samle navngitte privatpersoner — og «vi fant det på nett» er ikke
    et rettslig grunnlag uansett hvor kort det skrives.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_TEKST)
    if len(verdi) < MIN_FORMAAL:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _personer(kropp, rid):
    """Navn, roller og slettefrister som TRE LISTER AV SAMME LENGDE.

    Døra skriver posten og personene i én setning, så listene må være
    like lange der. Kappingen skjer ALDRI stille: en person som falt
    bort i kappingen ville stått i registeret uten en slettefrist, og
    det er nøyaktig det modulen finnes for å hindre.
    """
    from .policyadmin_http import _Avbrudd, _feil
    rader = kropp.get("personer")
    if not isinstance(rader, list) or not rader:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if len(rader) > MAKS_PERSONER:
        raise _Avbrudd(_feil("request_feilformet", rid))
    navn, roller, frister = [], [], []
    for rad in rader:
        if not isinstance(rad, dict):
            raise _Avbrudd(_feil("request_feilformet", rid))
        navn.append(_tekst(rad, "navn", rid, MAKS_NAVN))
        roller.append(_valg(rad, "rolle", rid, ROLLER))
        frister.append(_dato(rad, "slettefrist", rid))
    return navn, roller, frister


def _doerfeil(e, rid):
    """Dørenes dommer → API-feil. Samme form som 112–124."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if ("journalpost_unik" in str(e)
                or "journalkilde_unik" in str(e)):
            return _Avbrudd(_feil("journal_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: post uten krav, avviklet kilde,
        # slettefrist over taket, NULL/ulik lengde, og de to funnene
        # sveipen eier.
        return _Avbrudd(_feil("journal_ulovlig_tilstand", rid, 409))
    return None


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def _kilderad(r) -> dict:
    return {
        "kilde_id": str(r[0]), "organ": r[1], "organnummer": r[2],
        "format": r[3], "versjon": r[4],
        "gyldig_fra": r[5].isoformat(),
        "gyldig_til": r[6].isoformat() if r[6] else None,
        # GYLDIGHETEN REGNES I BASEN. To lesere skal ikke kunne komme
        # til hver sin konklusjon om hvorvidt formatet fortsatt gjelder.
        "gyldig_naa": r[7], "dogn_til_utlop": r[8],
        "innhold_sha256": r[9], "kilde_url": r[10],
        "antall_poster": r[11],
    }


def _postrad(r) -> dict:
    return {
        "post_id": str(r[0]), "sak_id": str(r[1]), "saktittel": r[2],
        "journalnummer": r[3], "journaldato": r[4].isoformat(),
        "dokumenttittel": r[5],
        # FORMÅLET FØLGER RADEN. En liste uten det ville vært en liste
        # over oppslag ingen kan gjøre rede for.
        "formaal": r[6],
        "organ": r[7], "format": r[8], "kildeversjon": r[9],
        "kilde_gyldig_naa": r[10],
        # ET MENNESKE HENTET DEN.
        "hentet_av_person": r[11], "hentet_dato": r[12].isoformat(),
        "antall_personer": r[13], "antall_levende": r[14],
        "naermeste_slettefrist": (r[15].isoformat() if r[15]
                                  else None),
        "kravversjon": r[16], "registrert": r[17].isoformat(),
    }


def svar_for(conn, tenant: str) -> dict:
    """Journalflatens tilstand i én transaksjon, gjennom fire dører."""
    s = conn.execute("SELECT * FROM m50_bildet(%s,%s)",
                     (tenant, MAKS_POSTER)).fetchone()
    kilder = [_kilderad(r) for r in conn.execute(
        "SELECT * FROM m50_kildene(%s,%s)",
        (tenant, MAKS_KILDER)).fetchall()]
    saker = [
        {"sak_id": str(r[0]), "tittel": r[1], "formaal": r[2],
         "grunnlag": r[3], "opprettet": r[4].isoformat(),
         "opprettet_av": r[5], "antall_poster": r[6],
         "antall_personer": r[7]}
        for r in conn.execute("SELECT * FROM m50_sakene(%s,%s)",
                              (tenant, MAKS_SAKER)).fetchall()]
    poster = [_postrad(r) for r in conn.execute(
        "SELECT * FROM m50_postene(%s,%s)",
        (tenant, MAKS_POSTER)).fetchall()]
    return {
        "sammendrag": {
            "saker": s[0], "poster": s[1], "personer": s[2],
            "levende_personer": s[3],
            # DET ENE TALLET MODULEN FINNES FOR: navngitte
            # privatpersoner vi oppbevarer lenger enn vi selv har
            # bestemt.
            "frist_passert": s[4], "frist_naer": s[5],
            "kilder": s[6], "gyldige": s[7], "utlopte": s[8],
            "apne_funn": s[9], "har_krav": s[10],
            "sletteplan_maks_dogn": s[11], "kravversjon": s[14],
            "vist": s[15],
        },
        "krav": ({"sletteplan_maks_dogn": s[11],
                  "slettevarsel_dogn": s[12],
                  "kildevarsel_dogn": s[13],
                  "versjon": s[14]}
                 if s[10] else None),
        "kilder": kilder,
        "saker": saker,
        "poster": poster,
    }


def journalbilde(tjeneste, request):
    """GET /v1/journal (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def kilder_endepunkt(tjeneste, request):
    """GET /v1/journal/kilder (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m50_kildene(%s,%s)",
                             (auth.tenant, MAKS_KILDER)).fetchall()
        svar = {"request_id": rid, "vist": len(rader),
                "grense": MAKS_KILDER,
                "kilder": [_kilderad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def poster_endepunkt(tjeneste, request):
    """GET /v1/journal/poster (okonomi:read).

    DEN NÆRMESTE SLETTEFRISTEN FØRST, og de passerte aller først.
    Sorteringen skjer i basen: en liste sortert på
    registreringstidspunkt ville begravd bruddet under alt som er i
    orden.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m50_postene(%s,%s)",
                             (auth.tenant, MAKS_POSTER)).fetchall()
        svar = {"request_id": rid, "vist": len(rader),
                "grense": MAKS_POSTER,
                "poster": [_postrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def personer_endepunkt(tjeneste, request):
    """GET /v1/journal/post/{post_id}/personer (okonomi:read).

    `navn` ER `null` ETTER ANONYMISERING, og det er ikke et hull i
    svaret — det ER svaret: raden er et spor av en behandling, ikke en
    person.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        pid = _sti_uuid(request, "post_id", rid)
        rader = conn.execute("SELECT * FROM m50_personene(%s,%s)",
                             (auth.tenant, pid)).fetchall()
        svar = {"post_id": str(pid), "request_id": rid, "personer": [
            {"person_id": str(r[0]), "navn": r[1], "rolle": r[2],
             "slettefrist": r[3].isoformat(),
             "dogn_til_slettefrist": r[4],
             "anonymisert_ts": (r[5].isoformat() if r[5] else None),
             "anonymisert_av": r[6],
             "registrert": r[7].isoformat(), "registrert_av": r[8]}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/journal/funn (okonomi:read).

    `kan_lukkes` KOMMER FRA BASEN. To funntyper lukkes bare av
    sveipen, og regelen bor ÉTT sted (`m50_funn_er_sveipens`).
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m50_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "funn": [
            {"funn_id": str(r[0]), "funntype": r[1],
             "kilde_id": str(r[2]) if r[2] else None,
             "post_id": str(r[3]) if r[3] else None,
             "person_id": str(r[4]) if r[4] else None,
             "organ": r[5], "kildeversjon": r[6],
             "journalnummer": r[7], "rolle": r[8],
             "slettefrist": r[9].isoformat() if r[9] else None,
             "over_grense": r[10], "detalj": r[11],
             "kravversjon": r[12], "kan_lukkes": r[13],
             "forst_sett": r[14].isoformat(),
             "sist_sett_sveip": r[15].isoformat(), "apen": r[16],
             "lukket_ts": r[17].isoformat() if r[17] else None,
             "lukket_av": r[18], "lukkenotat": r[19]}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen seks av de åtte skriveveiene deler.

    `/post` bruker den IKKE: den returnerer en RAD med antall personer,
    kildeversjonen og kravversjonen — den som registrerer et oppslag
    skal se hvor mange navngitte privatpersoner det førte med seg.
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
    """POST /v1/journal/krav (bestilling:opprett, idem).

    HVOR LENGE VI KAN OPPBEVARE ER TENANTENS BESLUTNING. En kommune som
    følger med på egne saker og et byrå som kartlegger et marked har
    ikke samme grunnlag — og et tak vi satte for dem ville vært en
    fullmakt modulen ga seg selv over kundens etterlevelse.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        maks = _heltall(kropp, "sletteplan_maks_dogn", rid,
                        *KRAVGRENSER["sletteplan_maks_dogn"])
        varsel = _heltall(kropp, "slettevarsel_dogn", rid,
                          *KRAVGRENSER["slettevarsel_dogn"])
        kilde = _heltall(kropp, "kildevarsel_dogn", rid,
                         *KRAVGRENSER["kildevarsel_dogn"])
        return ("SELECT versjon FROM m50_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, maks, varsel, kilde, bid, nokkel), {},
                "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_kilde_endepunkt(tjeneste, request):
    """POST /v1/journal/kilde (bestilling:opprett, idem).

    EN ALT AVVIKLET KILDEVERSJON KAN REGISTRERES: arkivet skal kunne
    svare på hvilket format vi leste noe i den gangen. Skillet går ved
    POSTEN — `/post` nekter mot en versjon som ikke gjelder i dag.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        organ = _tekst(kropp, "organ", rid, MAKS_NAVN)
        orgnr = _organnummer(kropp, "organnummer", rid)
        fmt = _valg(kropp, "format", rid, FORMATER)
        versjon = _tekst(kropp, "versjon", rid, MAKS_NAVN)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        sha = _sha256(kropp, "innhold_sha256", rid)
        url = _tekst_valgfri(kropp, "kilde_url", rid, MAKS_URL)
        kid = _utled("kilde", tenant, nokkel)
        return ("SELECT gyldig_naa FROM m50_registrer_kilde("
                "%s,%s,%s,%s,%s,%s,%s::date,%s::date,%s,%s,%s)",
                (tenant, kid, organ, orgnr, fmt, versjon, fra, til,
                 sha, url, bid),
                {"kilde_id": str(kid)}, "gyldig_naa")
    return _skriv(tjeneste, request, bygg)


def sett_gyldig_til_endepunkt(tjeneste, request):
    """POST /v1/journal/kilde/{kilde_id}/gyldig-til.

    NØKKELEN MÅ STÅ (121s lærdom). En klient som GLEMMER feltet ville
    ellers stilltiende gjort «avvikles 31. desember» om til «gjelder
    fortsatt».
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        kid = _sti_uuid(request, "kilde_id", rid)
        til = _dato_som_kan_vaere_null(kropp, "gyldig_til", rid)
        return ("SELECT gyldig_naa FROM m50_sett_gyldig_til("
                "%s,%s,%s::date,%s)",
                (tenant, kid, til, bid),
                {"kilde_id": str(kid), "gyldig_til": til},
                "gyldig_naa")
    return _skriv(tjeneste, request, bygg)


def opprett_sak_endepunkt(tjeneste, request):
    """POST /v1/journal/sak (bestilling:opprett, idem).

    FORMÅLET STÅR PÅ SAKEN, og det er ikke en kolonne til pynt: en
    sammenstilling er en behandling, og det er sporet som må kunne
    gjøre rede for seg. «Vi fant det på nett» er ikke et rettslig
    grunnlag — derfor er `grunnlag` en lukket liste over hjemler.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        tittel = _tekst(kropp, "tittel", rid, MAKS_NAVN)
        formaal = _formaal(kropp, "formaal", rid)
        grunnlag = _valg(kropp, "grunnlag", rid, GRUNNLAG)
        sid = _utled("sak", tenant, nokkel)
        return ("SELECT grunnlag FROM m50_opprett_sak("
                "%s,%s,%s,%s,%s,%s)",
                (tenant, sid, tittel, formaal, grunnlag, bid),
                {"sak_id": str(sid)}, "grunnlag")
    return _skriv(tjeneste, request, bygg)


def anonymiser_endepunkt(tjeneste, request):
    """POST /v1/journal/person/{person_id}/anonymiser.

    HANDLINGEN SOM LUKKER MODULENS EGET FUNN. Ikke sletting: at vi HAR
    oppbevart noen skal fortsatt kunne leses av den som spør — men uten
    navnet. Raden blir et spor av en behandling, ikke en person.

    IDEMPOTENT: en gjentatt kjøring på en alt anonymisert rad svarer
    `var_alt_anonymisert`, ikke en feil. Et menneske som trykker to
    ganger skal ikke få en feilmelding om noe som er i orden.
    """
    def bygg(tenant, bid, _nokkel, _kropp, rid, request):
        pid = _sti_uuid(request, "person_id", rid)
        return ("SELECT var_alt_anonymisert FROM m50_anonymiser("
                "%s,%s,%s)",
                (tenant, pid, bid),
                {"person_id": str(pid), "anonymisert": True},
                "var_alt_anonymisert")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/journal/funn/{funn_id}/lukk (bestilling:opprett).

    TO FUNNTYPER NEKTES AV DØRA. `slettefrist_passert` er ikke en
    mening man kan være uenig i: vi oppbevarer en navngitt
    privatperson lenger enn vi SELV har bestemt. Å klikke det bort
    ville vært å skru av det ene varselet som sier at vi bryter vår
    egen sletteplan.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "funn_id", rid)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT apen FROM m50_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, notat, bid),
                {"funn_id": str(fid)}, "apen")
    return _skriv(tjeneste, request, bygg)


def registrer_post_endepunkt(tjeneste, request):
    """POST /v1/journal/post (bestilling:opprett, idem).

    EGEN RAMME, fordi svaret er en RAD. Den som registrerer et oppslag
    skal se HVOR MANGE navngitte privatpersoner det førte med seg, og
    hvilken kildeversjon det ble lest i.

    PERSONENE FØLGER I SAMME KALL. Det er ikke en bekvemmelighet: døra
    skriver posten og personene i én setning, så en journalpost med
    navngitte privatpersoner ikke kan eksistere uten slettefrister —
    heller ikke i et vindu mellom to kall.
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
        sid = _kropp_uuid(kropp, "sak_id", rid)
        kid = _kropp_uuid(kropp, "kilde_id", rid)
        journalnr = _tekst(kropp, "journalnummer", rid, MAKS_REF)
        journaldato = _dato(kropp, "journaldato", rid)
        tittel = _tekst(kropp, "dokumenttittel", rid, MAKS_NAVN)
        formaal = _formaal(kropp, "formaal", rid)
        hentet_av = _tekst(kropp, "hentet_av_person", rid, MAKS_NAVN)
        hentet_dato = _dato(kropp, "hentet_dato", rid)
        navn, roller, frister = _personer(kropp, rid)
        pid = _utled("post", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m50_registrer_post("
                "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s::date,"
                "%s,%s,%s::date[],%s)",
                (tenant, pid, sid, kid, journalnr, journaldato,
                 tittel, formaal, hentet_av, hentet_dato,
                 navn, roller, frister, bid)).fetchone()
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
        return _ok({"post_id": str(pid),
                    "antall_personer": rad[1],
                    "organ": rad[2], "format": rad[3],
                    "kildeversjon": rad[4],
                    "kravversjon": rad[5]}, rid)

    return _med_conn(tjeneste, rid, kjor)
