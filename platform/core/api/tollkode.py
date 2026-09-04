"""M-52 toll- og HS-kodeagentens API (migrasjon 122).

Seksten endepunkter: åtte leseveier og åtte skriveveier, alle mot
dører. Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_tollkode_eier`-eid SECURITY DEFINER-dør i 122, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM DEKLARERER NOE, og det er ikke en sjekk her:
122 har ingen «deklarert»-kolonne, ingen mottaker og ingen utboks.

EN HS-KODE ER EN RETTSLIG PÅSTAND OM HVA EN VARE ER. Feil kode gir bot,
ikke bare forsinkelse — og boten treffer KUNDEN, ikke oss.

TRE NEKT SOM ER VERDT Å KJENNE:

  * `POST /forslag` NEKTER uten minst én grunn. Ikke fordi API-et
    sjekker det, men fordi `m52_avgi_forslag` skriver forslaget og
    grunnene i SAMME setning — et forslag uten grunnlag kan ikke
    oppstå, heller ikke i et vindu mellom to kall.

  * `POST /forslag` NEKTER mot en avviklet nomenklatur, og under
    tenantens sikkerhetsterskel. Et forslag under terskelen avgis
    ikke: varen blir stående uten, så noen ser at den ikke lot seg
    klassifisere.

  * `POST /klart` NEKTER hvis terskelen er HEVET siden forslaget kom.
    Å merke det klart ville vært å be et menneske deklarere på et
    grunnlag tenanten selv har forkastet.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett` — samme
presedens som 096/100–121.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en. For
forslaget er det nødvendig av en egen grunn: en gjentatt POST må ikke
bli to klassifiseringer av samme vare mot samme regelverk.
"""
from __future__ import annotations

import datetime
import uuid as uuidlib

import psycopg

MAKS_NOMENKLATURER = 200
MAKS_VARENUMMER = 2000
MAKS_VARER = 200
MAKS_GRUNNER = 20
MAKS_TEKST = 4000
MAKS_NAVN = 500
MAKS_URL = 2000
MAKS_REF = 200

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 122.
KRAVGRENSER = {
    "sikkerhetsterskel": (1, 100),
    "utlopsvarsel_dogn": (1, 730),
    "forslagsfrist_dogn": (1, 365),
}

SYSTEMER = ("hs", "kn", "tolltariff")
GRUNNARTER = ("bindende_forhandsuttalelse", "tidligere_klassifisering",
              "nomenklaturtekst", "alminnelig_fortolkningsregel",
              "faglig_vurdering")
FUNNTYPER = ("nomenklatur_utlopt", "nomenklatur_utloper_snart",
             "forslag_mot_utlopt_nomenklatur", "vare_uten_forslag",
             "forslag_under_terskel", "forslag_ikke_klart",
             "ingen_krav")

#: TOLLSATSEN I BASISPUNKTER, HELTALL (106s dom). En sats i flyttall
#: ville gjort «hva koster feilen» til et spørsmål med to svar.
MAKS_BP = 1_000_000

_M52_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m52:tollkode")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M52_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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
    """NULL er et ærlig svar. `materiale` og `bruk` er nettopp det
    nomenklaturen klassifiserer på, og en tom streng ville sett ut som
    et svar på et spørsmål ingen har stilt."""
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


def _bp_valgfri(kropp, felt: str, rid) -> int | None:
    """NULL BETYR «IKKE REGISTRERT HER», IKKE «NULL TOLL».

    Skillet er hele forskjellen mellom en vare som er tollfri og en vi
    ikke vet satsen på — og bare den ene av dem er trygg å deklarere.
    """
    if kropp.get(felt) is None:
        return None
    return _heltall(kropp, felt, rid, 0, MAKS_BP)


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
    samme, og det er riktig: en nomenklatur uten avviklingsdato
    gjelder fortsatt."""
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


def _url_valgfri(kropp, felt: str, rid) -> str | None:
    from .policyadmin_http import _Avbrudd, _feil
    if kropp.get(felt) is None:
        return None
    verdi = _tekst(kropp, felt, rid, MAKS_URL)
    if not (verdi.startswith("http://")
            or verdi.startswith("https://")):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if any(c.isspace() for c in verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _land_valgfri(kropp, felt: str, rid) -> str | None:
    """ISO 3166-1 alpha-2. Opprinnelseslandet avgjør preferansetoll og
    er derfor en del av «hva koster feilen»."""
    from .policyadmin_http import _Avbrudd, _feil
    if kropp.get(felt) is None:
        return None
    verdi = _tekst(kropp, felt, rid, 2).upper()
    if len(verdi) != 2 or not verdi.isalpha():
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _grunner(kropp, rid):
    """GRUNNENE, som fire lister av SAMME lengde.

    MINST ÉN. Døra nekter uansett — men denne sjekken gjør fraværet til
    en 400 og ikke en 409, og den sier hvorfor: et forslag uten
    grunnlag produserer falsk trygghet.
    """
    from .policyadmin_http import _Avbrudd, _feil
    rader = kropp.get("grunner")
    if not isinstance(rader, list) or not rader:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if len(rader) > MAKS_GRUNNER:
        raise _Avbrudd(_feil("request_feilformet", rid))
    arter, henvisninger, utdrag, datoer = [], [], [], []
    for rad in rader:
        if not isinstance(rad, dict):
            raise _Avbrudd(_feil("request_feilformet", rid))
        arter.append(_valg(rad, "art", rid, GRUNNARTER))
        henvisninger.append(_tekst(rad, "henvisning", rid, MAKS_NAVN))
        u = _tekst(rad, "utdrag", rid, MAKS_TEKST)
        if len(u) < 4:
            raise _Avbrudd(_feil("request_feilformet", rid))
        utdrag.append(u)
        datoer.append(_dato_valgfri(rad, "grunn_dato", rid))
    return arter, henvisninger, utdrag, datoer


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
    """Dørenes dommer → API-feil. Samme form som 112–121."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if ("tollforslag_unik" in str(e)
                or "nomenklatur_unik" in str(e)
                or "varenummer_unik" in str(e)
                or "tollvare_unik" in str(e)
                or "forslagsgrunn_unik" in str(e)):
            return _Avbrudd(_feil("toll_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: manglende grunnlag, avviklet
        # nomenklatur, sikkerhet under terskel, hevet terskel.
        return _Avbrudd(_feil("toll_ulovlig_tilstand", rid, 409))
    if isinstance(e, (psycopg.errors.IntegrityConstraintViolation,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.InsufficientPrivilege)):
        return _Avbrudd(_feil("toll_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Tollflatens tilstand i én transaksjon, gjennom fire dører."""
    s = conn.execute("SELECT * FROM m52_tollstatus(%s)",
                     (tenant,)).fetchone()
    nomenklaturer = [
        {"nomenklatur_id": str(r[0]), "system": r[1],
         "versjon": r[2], "gyldig_fra": r[3].isoformat(),
         "gyldig_til": r[4].isoformat() if r[4] else None,
         # GYLDIGHETEN REGNES I BASEN. To lesere skal ikke kunne komme
         # til hver sin konklusjon om hvorvidt regelverket vi
         # klassifiserer mot fortsatt gjelder.
         "gyldig_naa": r[5], "dogn_til_utlop": r[6],
         "innhold_sha256": r[7], "kilde_url": r[8],
         "registrert": r[9].isoformat(), "registrert_av": r[10],
         "antall_varenummer": r[11], "antall_forslag": r[12]}
        for r in conn.execute(
            "SELECT * FROM m52_nomenklaturene(%s,%s)",
            (tenant, MAKS_NOMENKLATURER)).fetchall()]
    varer = [_varerad(r) for r in conn.execute(
        "SELECT * FROM m52_varene(%s,%s)",
        (tenant, MAKS_VARER)).fetchall()]
    k = conn.execute("SELECT * FROM m52_kravene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "nomenklaturer": s[0], "gyldige": s[1], "utlopte": s[2],
            "varenummer": s[3], "varer": s[4],
            "klassifiserte": s[5], "uklassifiserte": s[6],
            "over_terskel": s[7], "klare": s[8],
            # DET ENE TALLET KLYNGEN FINNES FOR: koder som hviler på
            # et regelverk som siden er avviklet.
            "forslag_under_utlopt": s[9], "apne_funn": s[10],
            "har_krav": s[11], "terskel": s[12],
            "kravversjon": s[13], "vist": len(varer)},
        "nomenklaturer": nomenklaturer,
        "varer": varer,
        "krav": None if k is None else {
            "sikkerhetsterskel": k[0], "utlopsvarsel_dogn": k[1],
            "forslagsfrist_dogn": k[2], "versjon": k[3],
            "oppdatert": k[4].isoformat(), "oppdatert_av": k[5]}}


def _varerad(r) -> dict:
    """Varen MED nyeste forslag, nomenklaturversjonen det hviler på og
    ANTALL GRUNNER. De tre siste står her og ikke bak et ekstra
    oppslag: en kode uten versjonen og uten hvor mange grunner den har,
    er nettopp det modulen finnes for å unngå."""
    return {
        "vare_id": str(r[0]), "ekstern_ref": r[1],
        "beskrivelse": r[2], "materiale": r[3], "bruk": r[4],
        "opprinnelsesland": r[5], "registrert": r[6].isoformat(),
        "registrert_av": r[7],
        "forslag_id": str(r[8]) if r[8] else None,
        "system": r[9], "versjon": r[10], "kode": r[11],
        "tollsats_bp": r[12], "sikkerhet": r[13],
        "terskel_brukt": r[14], "over_terskel": r[15],
        "antall_grunner": r[16], "klar_til_deklarering": r[17],
        "klar_ts": r[18].isoformat() if r[18] else None,
        "klar_av": r[19],
        "avgitt": r[20].isoformat() if r[20] else None,
        "nomenklatur_gyldig_naa": r[21], "antall_forslag": r[22]}


def tollbilde(tjeneste, request):
    """GET /v1/toll (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def varenummer_endepunkt(tjeneste, request):
    """GET /v1/toll/nomenklatur/{nomenklatur_id}/varenummer."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        nid = _sti_uuid(request, "nomenklatur_id", rid)
        rader = conn.execute(
            "SELECT * FROM m52_varenumrene(%s,%s,%s)",
            (auth.tenant, nid, MAKS_VARENUMMER)).fetchall()
        # AVKORTINGEN SIES (CodeRabbit). En ekte HS-nomenklatur har
        # flere posisjoner enn taket her, og en stille avkortet liste
        # er verre enn en tom: den som ikke finner koden sin tror den
        # ikke finnes, i stedet for å vite at han ikke har sett alle.
        svar = {"nomenklatur_id": str(nid), "request_id": rid,
                "vist": len(rader), "grense": MAKS_VARENUMMER,
                "varenummer": [
                    {"varenummer_id": str(r[0]), "kode": r[1],
                     "tekst": r[2], "tollsats_bp": r[3],
                     "registrert": r[4].isoformat(),
                     "registrert_av": r[5],
                     "brukt_i_forslag": r[6]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def grunner_endepunkt(tjeneste, request):
    """GET /v1/toll/forslag/{forslag_id}/grunner (okonomi:read).

    REKKEFØLGEN ER RETTSKILDENES: en bindende forhåndsuttalelse veier
    tyngre enn en egen tidligere klassifisering, som veier tyngre enn
    en tekstlikhet. Den som leser skal se det tyngste først.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        fid = _sti_uuid(request, "forslag_id", rid)
        rader = conn.execute("SELECT * FROM m52_grunnene(%s,%s)",
                             (auth.tenant, fid)).fetchall()
        svar = {"forslag_id": str(fid), "request_id": rid,
                "grunner": [
                    {"grunn_id": str(r[0]), "art": r[1],
                     "henvisning": r[2], "utdrag": r[3],
                     "grunn_dato": r[4].isoformat() if r[4] else None,
                     "registrert": r[5].isoformat(),
                     "registrert_av": r[6]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def forslag_endepunkt(tjeneste, request):
    """GET /v1/toll/vare/{vare_id}/forslag (okonomi:read).

    HELE REKKEN, ikke bare den nyeste. En ny nomenklaturversjon gir en
    ny rad, og det er der «hva var riktig kode den gangen» står.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        vid = _sti_uuid(request, "vare_id", rid)
        rader = conn.execute("SELECT * FROM m52_forslagene(%s,%s)",
                             (auth.tenant, vid)).fetchall()
        svar = {"vare_id": str(vid), "request_id": rid,
                "forslag": [
                    {"forslag_id": str(r[0]), "system": r[1],
                     "versjon": r[2], "kode": r[3],
                     "sikkerhet": r[4], "terskel_brukt": r[5],
                     "over_terskel": r[6], "antall_grunner": r[7],
                     "nomenklatur_gyldig_naa": r[8],
                     "klar_til_deklarering": r[9],
                     "avgitt": r[10].isoformat(),
                     "avgitt_av": r[11]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/toll/funn (okonomi:read) — nattens funn."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m52_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "funn": [
            {"funn_id": str(r[0]), "funntype": r[1],
             "nomenklatur_id": str(r[2]) if r[2] else None,
             "vare_id": str(r[3]) if r[3] else None,
             "forslag_id": str(r[4]) if r[4] else None,
             "system": r[5], "nomenklaturversjon": r[6],
             "ekstern_ref": r[7], "over_grense": r[8],
             "detalj": r[9], "sikkerhet": r[10],
             "terskel_brukt": r[11], "kravversjon": r[12],
             "forst_sett": r[13].isoformat(),
             "sist_sett_sveip": r[14].isoformat(), "apen": r[15],
             "lukket_ts": r[16].isoformat() if r[16] else None,
             "lukket_av": r[17], "lukkenotat": r[18]}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen sju av de åtte skriveveiene deler.

    `/forslag` bruker den IKKE: den returnerer en RAD (sikkerhet,
    terskel, dommen, antall grunner, kode), ikke en skalar. Den som
    klassifiserer skal se hva forslaget SIER — særlig hvor mange
    grunner det hviler på.
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
    """POST /v1/toll/krav (bestilling:opprett, idem).

    SIKKERHETSTERSKELEN ER TENANTENS. En importør med tusen kolliposter
    i uka og en med tre har ikke samme toleranse for å ta feil, og en
    konstant her ville vært en fullmakt modulen ga seg selv over
    kundens bøter.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        terskel = _heltall(kropp, "sikkerhetsterskel", rid,
                           *KRAVGRENSER["sikkerhetsterskel"])
        utlop = _heltall(kropp, "utlopsvarsel_dogn", rid,
                         *KRAVGRENSER["utlopsvarsel_dogn"])
        frist = _heltall(kropp, "forslagsfrist_dogn", rid,
                         *KRAVGRENSER["forslagsfrist_dogn"])
        return ("SELECT m52_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, terskel, utlop, frist, bid, nokkel), {},
                "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_nomenklatur_endepunkt(tjeneste, request):
    """POST /v1/toll/nomenklatur (bestilling:opprett, idem).

    ET ALT AVVIKLET REGELVERK KAN REGISTRERES, og det er med vilje: en
    klassifisering fra 2022 må kunne forstås mot nomenklaturen som
    gjaldt DA. Skillet går ved FORSLAGET — `/forslag` nekter mot et
    avviklet sett.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        system = _valg(kropp, "system", rid, SYSTEMER)
        versjon = _tekst(kropp, "versjon", rid, MAKS_REF)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        sum_ = _sha256(kropp, "innhold_sha256", rid)
        url = _url_valgfri(kropp, "kilde_url", rid)
        nid = _utled("nomenklatur", tenant, nokkel)
        return ("SELECT m52_registrer_nomenklatur("
                "%s,%s,%s,%s,%s::date,%s::date,%s,%s,%s)",
                (tenant, nid, system, versjon, fra, til, sum_, url,
                 bid), {"nomenklatur_id": str(nid)}, None)
    return _skriv(tjeneste, request, bygg)


def sett_gyldig_til_endepunkt(tjeneste, request):
    """POST /v1/toll/nomenklatur/{nomenklatur_id}/gyldig-til.

    DENNE RUTEN FINNES FORDI REGELVERKET ER MYNDIGHETENS. Et tollvesen
    som kunngjør i juni at HS 2022 avvikles 31. desember, er nettopp
    den endringen modulen skal følge med på — og et helt frosset
    regelverk ville tvunget oss til å late som vi ikke visste.

    ALT ANNET ER FROSSET: system, versjon, `gyldig_fra` og
    innholdssummen er identiteten som gjør et gammelt forslag
    etterprøvbart.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        nid = _sti_uuid(request, "nomenklatur_id", rid)
        # NØKKELEN MÅ STÅ. En glemt `gyldig_til` ville stilltiende
        # gjort «avvikles 31. desember» om til «gjelder fortsatt».
        til = _dato_som_kan_vaere_null(kropp, "gyldig_til", rid)
        return ("SELECT m52_sett_gyldig_til(%s,%s,%s::date,%s)",
                (tenant, nid, til, bid),
                {"nomenklatur_id": str(nid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_varenummer_endepunkt(tjeneste, request):
    """POST /v1/toll/varenummer (bestilling:opprett, idem).

    POSISJONSTEKSTEN ER PÅKREVD. Det er DEN en klassifisering
    argumenteres mot — koden er bare en adresse.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        nid = _kropp_uuid(kropp, "nomenklatur_id", rid)
        kode = _tekst(kropp, "kode", rid, 20)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        sats = _bp_valgfri(kropp, "tollsats_bp", rid)
        vid = _utled("varenummer", tenant, nokkel)
        return ("SELECT m52_registrer_varenummer("
                "%s,%s,%s,%s,%s,%s,%s)",
                (tenant, vid, nid, kode, tekst, sats, bid),
                {"varenummer_id": str(vid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_vare_endepunkt(tjeneste, request):
    """POST /v1/toll/vare (bestilling:opprett, idem).

    MATERIALE OG BRUK ER EGNE FELTER, ikke fritekst i beskrivelsen: de
    to er nettopp det nomenklaturen klassifiserer på. En skrue av stål
    og en av plast havner i ulike kapitler.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        beskrivelse = _tekst(kropp, "beskrivelse", rid, MAKS_TEKST)
        materiale = _tekst_valgfri(kropp, "materiale", rid, MAKS_NAVN)
        bruk = _tekst_valgfri(kropp, "bruk", rid, MAKS_NAVN)
        land = _land_valgfri(kropp, "opprinnelsesland", rid)
        vid = _utled("vare", tenant, nokkel)
        return ("SELECT m52_registrer_vare(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, vid, ref, beskrivelse, materiale, bruk,
                 land, bid), {"vare_id": str(vid)}, None)
    return _skriv(tjeneste, request, bygg)


def avgi_forslag_endepunkt(tjeneste, request):
    """POST /v1/toll/vare/{vare_id}/forslag (bestilling:opprett).

    EGEN RAMME, fordi svaret er en RAD. Den som klassifiserer skal se
    hva forslaget SIER — sikkerheten, terskelen den ble målt mot, og
    ANTALL GRUNNER det hviler på.

    DØRA NEKTER uten minst én grunn, mot en avviklet nomenklatur, og
    under tenantens terskel. Grunnene skrives i SAMME setning som
    forslaget: et forslag uten grunnlag kan ikke oppstå, heller ikke i
    et vindu mellom to kall.
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
        vare_id = _sti_uuid(request, "vare_id", rid)
        varenummer_id = _kropp_uuid(kropp, "varenummer_id", rid)
        sikkerhet = _heltall(kropp, "sikkerhet", rid, 0, 100)
        arter, henv, utdrag, datoer = _grunner(kropp, rid)
        fid = _utled("forslag", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m52_avgi_forslag("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
                (tenant, fid, vare_id, varenummer_id, sikkerhet,
                 arter, henv, utdrag, datoer, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"vare_id": str(vare_id),
                    "forslag_id": str(fid), "sikkerhet": rad[0],
                    "terskel_brukt": rad[1], "over_terskel": rad[2],
                    "antall_grunner": rad[3], "system": rad[4],
                    "versjon": rad[5], "kode": rad[6]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def merk_klart_endepunkt(tjeneste, request):
    """POST /v1/toll/forslag/{forslag_id}/klart (bestilling:opprett).

    «KLAR TIL DEKLARERING» ER EN TILSTAND HOS OSS. Det finnes ingen
    deklarasjon her og ingen utsending.

    DØRA NEKTER hvis terskelen er HEVET siden forslaget kom, og hvis
    nomenklaturen er avviklet i mellomtiden. Å merke klart da ville
    vært å be et menneske deklarere på et grunnlag tenanten selv har
    forkastet.
    """
    def bygg(tenant, bid, _nokkel, _kropp, rid, request):
        fid = _sti_uuid(request, "forslag_id", rid)
        return ("SELECT m52_merk_klart(%s,%s,%s)",
                (tenant, fid, bid), {"forslag_id": str(fid)},
                "sikkerhet")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/toll/funn/{funn_id}/lukk (bestilling:opprett).

    `forslag_mot_utlopt_nomenklatur` NEKTES av døra. Det funnet
    forsvinner når varen klassifiseres på nytt mot en gyldig
    nomenklatur — og det er en HANDLING, ikke en mening.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "funn_id", rid)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m52_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, notat, bid), {"funn_id": str(fid)},
                None)
    return _skriv(tjeneste, request, bygg)
