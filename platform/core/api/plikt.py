"""M-21 avtale- og fristagentens API (migrasjon 096).

Fire endepunkter: én leseflate og tre skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en `disponit_plikt_eier`-
eid SECURITY DEFINER-dør i 096, og runtime har ingen tabellrettigheter i
det hele tatt (SP-7, 090/091-formen). At en frist ikke kan lukkes uten
kvittering, at et bortfall koster en begrunnelse og at en plikt ikke kan
registreres uten eier er derfor egenskaper ved BASEN, ikke ved denne
filen — og en flate som sjekket det selv ville vært en andre sannhet å
omgå.

SCOPENE ER GJENBRUKT, IKKE NYE. Registrering, kvittering og bortfall er
BESTILLINGER i plattformens forstand og bærer `bestilling:opprett` —
scopet `admin` allerede har, og som allerede står i
`BROWSER_MUTASJONSSCOPES`. Lesingen bærer `decisions:read`: den er
kundens egen tilstandsflate, samme klasse som utrullingsplanen og
rapportene, og `LESESCOPES`-porten i `test_pr008` krever uansett at en
`/v1/`-GET bærer et av de registrerte lesescopene. Et nytt scope skal
ikke oppstå av vane.

SP-2 PÅ REGISTRERINGEN (m35-formen): `plikt_id` utledes deterministisk av
Idempotency-Key-en, så en tapt respons + nytt klikk GJENSPILLER i stedet
for å føde en plikt til. Kvittering og bortfall er idempotente av
tilstanden sin — dørene avviser en plikt som ikke er `apen`.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Taket for hvor mange plikter leseflaten viser. Registerets tak er
#: dørens (500); dette er flatens, og det er en annen begrunnelse: et
#: register med tusen plikter skal ikke kunne gjøre ett HTTP-svar til en
#: nedlasting.
MAKS_PLIKTER = 200

#: Lengdegrensene på kundens egen tekst. `kilde` er en HENVISNING (en
#: avtale, en paragraf, et vedtaksnummer) og ikke et sammendrag, og
#: `kvittering_ref` er en referanse til noe som finnes et annet sted —
#: begge er korte av natur. Begrunnelsen for bortfall er den ene som
#: faktisk er en setning et menneske skal lese senere.
MAKS_TITTEL = 200
MAKS_KILDE = 500
MAKS_KVITTERING = 200
MAKS_BEGRUNNELSE = 2000

#: De lovlige gjentakelsene — SPEIL av CHECK-en i 096. Speilet finnes for
#: at feilen skal bli 400 og ikke 409: en ukjent verdi er en feilformet
#: forespørsel, ikke en tilstand som sier nei. Dørens CHECK er fortsatt
#: den bindende.
GJENTAKELSER = ("engang", "aarlig", "kvartalsvis", "manedlig")

#: SP-2-navnerommet for de deterministisk utledede id-ene (m8/m35-formen).
_M21_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m21:plikt")


def _utled(tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M21_NS, f"plikt\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _plikt_id(request, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get("plikt_id")))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


#: De ENESTE feilklassene dørene bruker som DOM (089s liste, samme
#: begrunnelse): en tapt forbindelse eller en manglende rettighet på selve
#: funksjonen er ikke «tilstanden nekter». Å svare 409 på dem ville
#: fortalt et menneske at plikten er i feil tilstand, mens sannheten er at
#: basen er nede. Uoversatte feil KASTES VIDERE, så `_med_conn` svarer
#: `db_utilgjengelig` og driftsloggen får dem.
#
#: `InsufficientPrivilege` står bevisst IKKE i listen, til forskjell fra
#: 089s (CodeRabbit). 089 trenger den fordi kontinuitetsvaktene feller
#: sine dommer med den ERRCODE-en gjennom dørene. M-21s vakt gjør ikke
#: det: hver vei den kan avvise — terminal status, manglende aktør,
#: frist bakover, frosset identitet — er ALT stengt av dørene selv, med
#: `invalid_parameter_value`. Da er en `insufficient_privilege` herfra
#: alltid det den ser ut som: et manglende grant eller en tapt
#: tenantkontekst. Det er en DRIFTSTILSTAND, og den skal bli 503 med en
#: driftslogg — ikke en 409 som forteller et menneske at plikten dens er
#: i feil tilstand mens sannheten er at basen mangler en rettighet.
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
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er på tom kvittering, tom begrunnelse, ukjent
        # eier og en plikt som ikke er `apen`. Kroppen ER velformet — det
        # er tilstanden eller innholdskravet som sier nei.
        return _Avbrudd(_feil("plikt_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        return _Avbrudd(_feil("plikt_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Hele leseflatens tilstand i én transaksjon, gjennom lesedøren.

    Rekkefølgen er DØRENS (åpne først, deretter frist stigende) — flaten
    sorterer ikke om. `dogn_til_frist` er regnet i BASEN, i samme skann
    som raden, nettopp for at flaten ikke skal trekke to tidspunkter fra
    hverandre.
    """
    plikter = [
        {"plikt_id": str(r[0]), "tittel": r[1], "eier_bruker_id": r[2],
         "eier_navn": r[3], "kilde": r[4], "frist": r[5].isoformat(),
         "dogn_til_frist": r[6], "gjentakelse": r[7], "status": r[8],
         "kvittering_ref": r[9],
         "lukket": r[10].isoformat() if r[10] is not None else None,
         "lukket_av": r[11], "bortfall_begrunnelse": r[12],
         "bortfalt": r[13].isoformat() if r[13] is not None else None,
         "bortfalt_av": r[14]}
        for r in conn.execute("SELECT * FROM m21_plikter(%s,%s)",
                              (tenant, MAKS_PLIKTER)).fetchall()]
    return {"plikter": plikter}


def plikter(tjeneste, request):
    """GET /v1/plikt (decisions:read) — tenantens eget pliktregister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


def registrer_endepunkt(tjeneste, request):
    """POST /v1/plikt (bestilling:opprett, idem) — registrer en plikt.

    EIEREN ER PÅKREVD I KROPPEN, ikke utledet av innloggingen. Den som
    registrerer en plikt er ofte ikke den som skal gjøre den, og en flate
    som stille satte innloggeren som eier ville gjort «plikter uten
    eier»-KPI-en sann på papiret og falsk i praksis. Døren avviser en
    eier som ikke er aktivt medlem av tenanten.
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
        tittel = _tekst(kropp, "tittel", rid, MAKS_TITTEL)
        kilde = _tekst(kropp, "kilde", rid, MAKS_KILDE)
        eier = _tekst(kropp, "eier_bruker_id", rid, 128)
        frist = kropp.get("frist")
        if not isinstance(frist, str) or not frist.strip():
            raise _Avbrudd(_feil("request_feilformet", rid))
        gjentakelse = kropp.get("gjentakelse", "engang")
        if gjentakelse not in GJENTAKELSER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        # Varslingspunktene er VALGFRIE: uten dem seeder døren husets
        # standard (30/7/1). Er de med, må de være hele døgn i dørens
        # eget spenn — en liste med en negativ verdi er en feilformet
        # forespørsel, ikke en tilstand.
        punkter = kropp.get("dogn_for")
        if punkter is not None:
            if not isinstance(punkter, list) or not punkter \
                    or not all(isinstance(d, int) and not isinstance(d, bool)
                               and 0 <= d <= 3650 for d in punkter):
                raise _Avbrudd(_feil("request_feilformet", rid))
            punkter = sorted(set(punkter))
        pid = _utled(tenant, nokkel)
        try:
            ny = conn.execute(
                "SELECT m21_registrer_plikt(%s,%s,%s,%s,%s,%s::timestamptz,"
                "                           %s,%s,%s)",
                (tenant, pid, tittel, eier, kilde, frist, gjentakelse,
                 punkter, _bid)).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            # Fristen er kallerens tekst, og castet skjer i basen. En
            # ulesbar eller umulig dato er 400, ikke 409: det er KROPPEN
            # som er feil, ikke tilstanden. Fanget som to navngitte
            # klasser og ikke som `DataError`, fordi `DataError` også
            # dekker 22023 — dørenes egen dom, som skal bli 409.
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        # `ny=false` er et STILLE JA (SP-2): samme nøkkel og samme
        # innhold ga samme plikt. Kalleren får den samme id-en, og
        # ingenting ble skrevet to ganger.
        return _ok({"plikt_id": str(pid), "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def lukk_endepunkt(tjeneste, request):
    """POST /v1/plikt/{plikt_id}/lukk (bestilling:opprett, idem).

    KVITTERINGEN HÅNDHEVES IKKE HER. Døren krever den, CHECK-en i 096
    krever den, og flaten krever den i skjemaet — men den bindende er
    basens. Et forsøk uten kvittering blir 409 fordi BASEN nektet, ikke
    fordi API-et sjekket.

    Svaret bærer `neste_frist` for en gjentakende plikt: kvitteringen
    gjelder FOREKOMSTEN, og plikten står videre med den neste fristen
    sin. `null` betyr at plikten er lukket for godt.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        _krev_idem(request, rid)
        kropp = _kropp(request)
        pid = _plikt_id(request, rid)
        kvittering = _tekst(kropp, "kvittering_ref", rid, MAKS_KVITTERING)
        try:
            neste = conn.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                                 (tenant, pid, kvittering,
                                  _bid)).fetchone()[0]
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"plikt_id": str(pid),
                    "neste_frist": neste.isoformat() if neste else None},
                   rid)

    return _med_conn(tjeneste, rid, kjor)


def bortfall_endepunkt(tjeneste, request):
    """POST /v1/plikt/{plikt_id}/bortfall (bestilling:opprett, idem).

    Den ANDRE lovlige utgangen: plikten gjelder ikke lenger. Den koster
    en skreven begrunnelse — uten den ville «bortfalt» vært en gratis vei
    ut av enhver frist, og registeret en liste over ting man kan klikke
    bort.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        _krev_idem(request, rid)
        kropp = _kropp(request)
        pid = _plikt_id(request, rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        try:
            conn.execute("SELECT m21_marker_bortfalt(%s,%s,%s,%s)",
                         (tenant, pid, begrunnelse, _bid))
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"plikt_id": str(pid), "bortfalt": True}, rid)

    return _med_conn(tjeneste, rid, kjor)
