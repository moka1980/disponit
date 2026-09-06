"""M-28 logistikk- og transportagentens API (migrasjon 139).

Ni endepunkter: fire leseveier og fem skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_transport_eier`-eid SECURITY DEFINER-dør i 139, og runtime
har ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM BESTILLER TRANSPORT.

Ingen `/bestill`, ingen `/book`, ingen `/ombook`, ingen `/etikett`.
BILEN KJØRER UANSETT HVA BASEN SIER: en booking som ble rullet tilbake
er fortsatt en bil på veien, en pakke i en terminal og en faktura fra
en transportør.

FAREKLASSEN OPPGIS AV ET MENNESKE, ALDRI UTLEDET.

`POST /kolli` krever `fareklasse_oppgitt_av` — et navn, ikke et flagg.
Ruta tar ikke imot en produktbeskrivelse, en varekode eller en
HS-kode, og kan derfor ikke regne klassen ut av dem. En gal påstand om
farlig gods er en brann i en lastebil, ikke en feil i en rapport.

HS-KODEN ER M-52s. En transportmodul som tok imot den ville før eller
siden utledet fareklassen av den, og da ville `fareklasse_oppgitt_av`
pekt på et menneske som aldri så pakken.

MOTTAKERLANDET OPPGIS IKKE. `POST /forslag` tar en ADRESSEVERSJON;
landet leses derfra, og adressen må ha en `adressekontroll` med
`utfall = 'godkjent'`. «Adresse og tjeneste valideres før booking» er
akseptansekravet — tjenesten finnes ikke, adressen gjør.

OG LANDET MÅ HA EN LANDPAKKE (M-32, 138). Et land uten pakke er et
land huset ikke har lest reglene for, og for farlig gods er det ikke
en formalitet. Døra nekter, og nektet er en 400.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `okonomi:read` — det samme M-24 og M-27 bruker: et kolli
og en plan hører til i vareflyten, ikke i sikkerhetsbildet.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import re
import uuid as uuidlib

import psycopg

MAKS_KOLLI = 200
MAKS_FORSLAG = 200
MAKS_FUNN = 200
MAKS_TEKST = 8000
MAKS_REF = 200
#: Speiler CHECK-ene i 139.
GRENSER = {
    "vekt_gram": (1, 100_000_000),
    "lengde_mm": (1, 20_000),
    "bredde_mm": (1, 20_000),
    "hoyde_mm": (1, 20_000),
    "maks_kolli_gram": (1, 100_000_000),
    "forslagsfrist_dogn": (1, 365),
}

#: ADRs NI KLASSER PLUSS `ingen`.
#:
#: Settet er den internasjonale standarden, ikke vår oppfinnelse — og
#: derfor komplett uten en `annet`-verdi. En `annet` her ville gjort
#: det lukkede settet til et åpent, og en pakke ingen visste hva var
#: ville fått lov til å reise.
FAREKLASSER = (
    "ingen",
    "klasse_1_eksplosiver",
    "klasse_2_gasser",
    "klasse_3_brannfarlige_vaesker",
    "klasse_4_brannfarlige_faste_stoffer",
    "klasse_5_oksiderende",
    "klasse_6_giftige_og_smittefarlige",
    "klasse_7_radioaktive",
    "klasse_8_etsende",
    "klasse_9_ovrige_farlige",
)

_M28_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m28:transport")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M28_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _landkode(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not re.fullmatch(r"[A-Z]{2}", verdi):
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


def _kropp_uuid(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(request.path_params[navn])
    except (KeyError, ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    «Tyskland har ingen landpakke» og «adressen har ingen godkjent
    kontroll» er begge noe kalleren kan gjøre noe med. En 500 ville
    sendt henne til driftsvakten framfor til den som skal kontrollere
    adressen eller felle en landpakke.
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
# RADBYGGERNE.
# ---------------------------------------------------------------------

def _bilderad(r) -> dict:
    return {
        "kolli": r[0], "farlige_kolli": r[1], "apne_forslag": r[2],
        "forkastede": r[3], "land_i_bruk": r[4],
        # DET VIKTIGSTE TALLET I MODULEN, OG DET ER ALLTID 0.
        #
        # Ikke en telling av en kolonne — en påstand om at kolonnen
        # ikke finnes. `transportforslag` har ingen `bestilt_ts`, ingen
        # `booking_ref` og ingen `sporingsnummer`.
        "bestillinger": r[5],
        "apne_funn": r[6], "har_krav": r[7], "avsenderland": r[8],
        "maks_kolli_gram": r[9],
        "manuell_kontroll_over_gram": r[10],
        "forslagsfrist_dogn": r[11], "kravversjon": r[12],
    }


def _kollirad(r) -> dict:
    return {
        "kolli_id": str(r[0]), "referanse": r[1], "vekt_gram": r[2],
        "lengde_mm": r[3], "bredde_mm": r[4], "hoyde_mm": r[5],
        "fareklasse": r[6], "farlig": r[7],
        # HVEM SOM SA DET. En fareklasse uten et navn bak er en påstand
        # ingen svarer for.
        "fareklasse_oppgitt_av": r[8],
        "har_apent_forslag": r[9],
        "registrert": r[10].isoformat(),
    }


def _forslagsrad(r) -> dict:
    return {
        "forslag_id": str(r[0]), "kolli_id": str(r[1]),
        "kolliref": r[2],
        "mottakerland": r[3], "avsenderland": r[4],
        # REGELVERSJONEN. En plan uten versjonen av reglene den hviler
        # på er en plan ingen kan etterprøve når reglene endres.
        "landpakke_regelversjon": r[5],
        "fareklasse": r[6], "farlig": r[7], "vekt_gram": r[8],
        "over_kontrollgrense": r[9], "status": r[10],
        "begrunnelse": r[11],
        "foreslatt_ts": r[12].isoformat(), "foreslatt_av": r[13],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1], "referanse": r[2],
        "detalj": r[3], "sveipens": r[4],
        "forst_sett": r[5].isoformat(),
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127/128/130/132-138s form)."""
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m28_bildet(%s)",
                         (tenant,)).fetchone()),
        "kolli": [_kollirad(r) for r in conn.execute(
            "SELECT * FROM m28_kolliene(%s,%s)",
            (tenant, MAKS_KOLLI)).fetchall()],
        "forslag": [_forslagsrad(r) for r in conn.execute(
            "SELECT * FROM m28_forslagene(%s,%s)",
            (tenant, MAKS_FORSLAG)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m28_transportfunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def transportbilde(tjeneste, request):
    """GET /v1/transport (okonomi:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def kolli_endepunkt(tjeneste, request):
    """GET /v1/transport/kolli (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m28_kolliene(%s,%s)",
                             (auth.tenant, MAKS_KOLLI)).fetchall()
        return kanonisk_json(
            {"kolli": [_kollirad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def forslag_endepunkt(tjeneste, request):
    """GET /v1/transport/forslag (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m28_forslagene(%s,%s)",
                             (auth.tenant, MAKS_FORSLAG)).fetchall()
        return kanonisk_json(
            {"forslag": [_forslagsrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/transport/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m28_transportfunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
#
# FEM, OG INGEN AV DEM BESTILLER NOE. `/krav` setter grensene,
# `/kolli` registrerer det et menneske har målt, `/forslag` lager en
# plan, `/forslag/{id}/forkast` vraker en, `/funn/{id}/lukk` lukker et
# funn noen har håndtert.
#
# DET FINNES INGEN SJETTE. Ingen `/bestill`, ingen `/ombook`, ingen
# `/etikett` — og de er utelatt fordi modulen ikke skal sende noe ut i
# verden, ikke fordi de var vanskelige.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen fire av de fem skriveveiene deler.

    `/forslag` står utenfor: svaret er mer enn en kvittering. Kalleren
    oppga verken mottakerland, landpakkeversjon eller fareklasse — alle
    tre leses av modulen, og alle tre må komme tilbake.
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
    """POST /v1/transport/krav (bestilling:opprett, idem).

    AVSENDERLANDET MÅ HA EN LANDPAKKE, av samme grunn som M-32s
    selgerland: uten leste regler for landet vi sender FRA, er
    ingenting av det som følger etterprøvbart.

    VERSJONEN TILDELES AV DØRA. Raden er append-only fordi forslagene
    peker på den (137/138s form).
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        land = _landkode(kropp, "avsenderland", rid)
        maks = _heltall(kropp, "maks_kolli_gram", rid,
                        *GRENSER["maks_kolli_gram"])
        manuell = _heltall(kropp, "manuell_kontroll_over_gram", rid, 0,
                           GRENSER["maks_kolli_gram"][1])
        frist = _heltall(kropp, "forslagsfrist_dogn", rid,
                         *GRENSER["forslagsfrist_dogn"])
        del nokkel
        return ("SELECT m28_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, land, maks, manuell, frist, bid), {},
                "kravversjon")
    return _skriv(tjeneste, request, bygg)


def registrer_kolli_endepunkt(tjeneste, request):
    """POST /v1/transport/kolli (bestilling:opprett, idem).

    ET MENNESKE HAR MÅLT DETTE.

    `fareklasse_oppgitt_av` er PÅKREVD og er et NAVN. Ruta tar ikke
    imot en produktbeskrivelse, en varekode eller en HS-kode, og kan
    derfor ikke regne klassen ut av dem — `fareklasse_utledet_av_maskin`
    er umulig fordi det ikke finnes noe å utlede den AV.

    MILLIMETER OG GRAM, I HELTALL. Flyttall og fysiske mål hører ikke
    sammen når noen skal laste en bil etter dem.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "referanse", rid, MAKS_REF)
        vekt = _heltall(kropp, "vekt_gram", rid, *GRENSER["vekt_gram"])
        lengde = _heltall(kropp, "lengde_mm", rid, *GRENSER["lengde_mm"])
        bredde = _heltall(kropp, "bredde_mm", rid, *GRENSER["bredde_mm"])
        hoyde = _heltall(kropp, "hoyde_mm", rid, *GRENSER["hoyde_mm"])
        klasse = _valg(kropp, "fareklasse", rid, FAREKLASSER)
        oppgitt_av = _tekst(kropp, "fareklasse_oppgitt_av", rid, MAKS_REF)
        kravversjon = _heltall(kropp, "kravversjon", rid, 1, 1_000_000)
        kid = _utled("kolli", tenant, nokkel)
        return ("SELECT m28_registrer_kolli(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s)",
                (tenant, kid, ref, vekt, lengde, bredde, hoyde, klasse,
                 oppgitt_av, kravversjon, bid),
                {"kolli_id": str(kid), "fareklasse": klasse}, None)
    return _skriv(tjeneste, request, bygg)


def foresla_endepunkt(tjeneste, request):
    """POST /v1/transport/forslag (bestilling:opprett, idem).

    MODULENS HOVEDDØR — og den som ikke bruker `_skriv`, fordi svaret
    er mer enn en kvittering.

    KALLEREN OPPGIR EN ADRESSEVERSJON, IKKE ET LAND. Mottakerlandet
    leses derfra, og adressen må ha en godkjent `adressekontroll`.
    Landpakkeversjonen og fareklassen leses av modulen — alle tre
    kommer tilbake i svaret, fordi kalleren ikke oppga noen av dem.

    OG DET FINNES INGEN `/bestill` ETTERPÅ. Forslaget er endestasjonen.
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
        kolli = _kropp_uuid(kropp, "kolli_id", rid)
        kravversjon = _heltall(kropp, "kravversjon", rid, 1, 1_000_000)
        adresse = _kropp_uuid(kropp, "adresseversjon_id", rid)
        grunn = _tekst(kropp, "begrunnelse", rid, MAKS_TEKST)
        fid = _utled("forslag", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m28_foresla(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, fid, kolli, kravversjon, adresse, grunn,
                 bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"forslag_id": str(fid), "mottakerland": rad[0],
                    "landpakke_regelversjon": rad[1],
                    "fareklasse": rad[2], "farlig": rad[3],
                    "krever_kontroll": rad[4]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def forkast_endepunkt(tjeneste, request):
    """POST /v1/transport/forslag/{forslag_id}/forkast.

    EN PLAN SOM BLE VRAKET, IKKE SLETTET. Sletting ville fjernet
    beviset på at vi hadde planen (M-50s dom, 124) — og et forkastet
    forslag sperrer ikke for et nytt.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        f = _sti_uuid(request_, "forslag_id", rid)
        grunn = _tekst(kropp, "grunn", rid, MAKS_TEKST)
        return ("SELECT m28_forkast(%s,%s,%s,%s)",
                (tenant, f, grunn, bid), {"forslag_id": str(f)}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/transport/funn/{funn_id}/lukk.

    OG DØRA NEKTER FOR SVEIPENS EGNE. Et menneske som kunne lukket
    `land_uten_pakke` ville lukket en måling og ikke en sak.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        f = _sti_uuid(request_, "funn_id", rid)
        grunn = _tekst(kropp, "grunn", rid, MAKS_TEKST)
        return ("SELECT m28_lukk_funn(%s,%s,%s,%s)",
                (tenant, f, grunn, bid), {"funn_id": str(f)}, None)
    return _skriv(tjeneste, request, bygg)
