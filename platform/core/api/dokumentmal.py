"""M-5 malregisterets API (094) — registeret og utfyllingen.

FEM ENDEPUNKTER, og de deler seg i to klasser som IKKE er «lesing» og
«skriving», men «det som endrer registeret» og «det som leser det»:

  GET  /v1/dokumentmal                                   decisions:read
  POST /v1/dokumentmal/familier                          bestilling:opprett
  POST /v1/dokumentmal/versjoner                         bestilling:opprett
  POST /v1/dokumentmal/versjon/{id}/publiser             bestilling:opprett
  POST /v1/dokumentmal/versjon/{id}/trekk-tilbake        bestilling:opprett
  POST /v1/dokumentmal/versjon/{id}/utfylling            decisions:read

SCOPEVALGET, og hvorfor det ikke er et nytt scope
-------------------------------------------------
M-5 GENERALISERER 079_utsendingstekst, og 079s to ruter står i RUTESCOPE
med `decisions:read` for lesing og `bestilling:opprett` for forfatting og
skjuling. Malregisteret er den samme myndigheten på et større lager:
kunden forfatter sin egen tekst, og administratoren er den som forvalter
den. Et nytt scope er en REGISTRERING i autorisasjonslaget — en ny linje
i `ROLLE_TIL_SCOPES`, en ny i `LESESCOPES` eller
`BROWSER_MUTASJONSSCOPES`, og en egen port i `test_rolle_scopes` — og det
skal begrunnes, ikke antas. Her finnes ingen myndighet 079 ikke allerede
har navngitt, så gjenbruken er den ærlige formen. (M-21 gjenbruker
`bestilling:opprett` av samme grunn.)

UTFYLLINGEN ER EN LESERUTE MED EN KROPP, og scopet SIER DET
-----------------------------------------------------------
`POST …/utfylling` bærer `decisions:read`, ikke et mutasjonsscope. Det er
ikke en forglemmelse — det er invarianten `utfylling_skrev_dokument`
uttrykt i autorisasjonslaget: kallet har LESEMYNDIGHET og ingenting
annet. Tre lag sier det samme, uavhengig av hverandre:

  * `m5_fyll_mal` er erklært STABLE — PostgreSQL avviser enhver skriving
    i kroppen, uansett hva noen en dag skriver der;
  * runtime har ingen INSERT/UPDATE/DELETE på noen av de fire tabellene
    (migrer.py gir kun SELECT);
  * ruten har et lesescope, så den kan ikke vokse en skriveevne uten at
    RUTESCOPE-linja endres i samme diff.

METODEN er POST og ikke GET fordi verdiene er KUNDENS DATA. En
utfyllingsverdi i en query-streng havner i tilgangslogger, i
browserhistorikk og i Referer-headere; en kropp gjør ikke det. At
POST-en likevel ikke skriver noe er dermed en egenskap ved de tre lagene
over, ikke ved verbet.

Alle fem POST-ene går gjennom `_browserkontekst` (auth + CSRF) og er
altså BROWSERVEIER. Ingen av dem har en maskinkaller i v1, og en rute
som ikke har en kaller skal ikke ha en autentiseringsvei heller.

SP-2 på de to som FØDER en rad (familie og versjon): id-ene UTLEDES
deterministisk av `Idempotency-Key` (m35-formen), så et gjenspill etter
en tapt respons treffer dørenes egen materialitetssjekk i stedet for å
føde en ny familie eller en ny versjon. Publisering og tilbaketrekking
er naturlig idempotente overganger på en id kalleren alt har — de krever
nøkkelen (husets regel for skriveruter), men utleder ingenting av den.
"""
from __future__ import annotations

import json
import uuid as uuidlib

import psycopg

#: Flatens tak, ikke registerets. Et malregister med tusen familier skal
#: ikke kunne gjøre ett HTTP-svar til en nedlasting — og en flate som
#: viser 100 familier med 20 versjoner hver er allerede for mye å lese.
MAKS_FAMILIER = 100
MAKS_VERSJONER = 20

#: Kroppsgrenser. Malene er MENNESKESKREVNE dokumenter, ikke datastrømmer.
MAKS_KOMPONENTER = 200
MAKS_FELT = 100
MAKS_VERDIER = 100

#: SP-2-navnerommet for de deterministisk utledede id-ene (m35-formen).
_M5_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m5:dokumentmal")


def _utled(tenant: str, nokkel: str, ledd: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M5_NS, f"{ledd}\x1f{tenant}\x1f{nokkel}")


# ---------------------------------------------------------------------
# Leseveien
# ---------------------------------------------------------------------

def svar_for(conn, tenant: str) -> dict:
    """Hele registeret i én transaksjon: familier → versjoner →
    komponenter + feltdeklarasjoner.

    Fire spørringer og ikke én join: en join over komponentene ville
    multiplisert feltradene med komponentradene, og flaten trenger begge
    listene HELE — feltdeklarasjonen er nettopp det som gjør at en
    manglende verdi kan navngis.
    """
    familier = [
        {"familie_id": str(r[0]), "navn": r[1], "beskrivelse": r[2],
         "opprettet": r[3].isoformat(), "opprettet_av": r[4],
         "versjoner": []}
        for r in conn.execute(
            "SELECT familie_id, navn, beskrivelse, opprettet, opprettet_av"
            "  FROM malfamilie WHERE tenant=%s"
            " ORDER BY navn, familie_id LIMIT %s",
            (tenant, MAKS_FAMILIER)).fetchall()]
    if not familier:
        return {"familier": []}

    fam_ider = [uuidlib.UUID(f["familie_id"]) for f in familier]
    fam_indeks = {f["familie_id"]: f for f in familier}

    # VINDUET GJELDER PER FAMILIE, ikke på tvers (089-lærdommen): en
    # familie med 500 versjoner skal ikke spise budsjettet og la naboene
    # komme tilbake tomme — en tom versjonsliste leses som «ingen mal
    # finnes», ikke som «vi viste deg ikke alt».
    versjoner = {}
    for r in conn.execute(
            "SELECT familie_id, versjon_id, versjonsnr, status, opprettet,"
            "       opprettet_av, publisert_ts, publisert_av,"
            "       tilbaketrukket_ts, tilbaketrukket_av"
            "  FROM (SELECT v.*, row_number() OVER ("
            "                 PARTITION BY familie_id"
            "                 ORDER BY versjonsnr DESC) AS n"
            "          FROM malversjon v"
            "         WHERE tenant=%s AND familie_id = ANY(%s)) q"
            " WHERE q.n <= %s ORDER BY familie_id, versjonsnr DESC",
            (tenant, fam_ider, MAKS_VERSJONER)).fetchall():
        v = {"versjon_id": str(r[1]), "versjonsnr": r[2], "status": r[3],
             "opprettet": r[4].isoformat(), "opprettet_av": r[5],
             "publisert": r[6].isoformat() if r[6] is not None else None,
             "publisert_av": r[7],
             "tilbaketrukket": r[8].isoformat() if r[8] is not None else None,
             "tilbaketrukket_av": r[9],
             "komponenter": [], "felt": []}
        fam_indeks[str(r[0])]["versjoner"].append(v)
        versjoner[v["versjon_id"]] = v

    if versjoner:
        v_ider = [uuidlib.UUID(k) for k in versjoner]
        for r in conn.execute(
                "SELECT versjon_id, rekkefolge, komponenttype, innhold,"
                "       feltnokkel, laast FROM malkomponent"
                " WHERE tenant=%s AND versjon_id = ANY(%s)"
                " ORDER BY versjon_id, rekkefolge", (tenant, v_ider)
        ).fetchall():
            versjoner[str(r[0])]["komponenter"].append(
                {"rekkefolge": r[1], "komponenttype": r[2], "innhold": r[3],
                 "feltnokkel": r[4], "laast": r[5]})
        for r in conn.execute(
                "SELECT versjon_id, feltnokkel, paakrevd, felttype,"
                "       beskrivelse FROM malfelt"
                " WHERE tenant=%s AND versjon_id = ANY(%s)"
                " ORDER BY versjon_id, feltnokkel", (tenant, v_ider)
        ).fetchall():
            versjoner[str(r[0])]["felt"].append(
                {"feltnokkel": r[1], "paakrevd": r[2], "felttype": r[3],
                 "beskrivelse": r[4]})

    return {"familier": familier}


def dokumentmal(tjeneste, request):
    """GET /v1/dokumentmal (decisions:read) — hele malregisteret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


# ---------------------------------------------------------------------
# Felles kroppslesing og feiloversettelse
# ---------------------------------------------------------------------

def _tekst(kropp, felt: str, rid, maks: int, *, paakrevd: bool = True):
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None and not paakrevd:
        return None
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _versjon_id(request, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get("versjon_id")))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _liste(kropp, felt: str, rid, maks: int, *, tom_ok: bool):
    """En JSON-liste av objekter, med tak. Innholdet valideres IKKE her:
    dørene skriver rått inn og CHECK-ene i 094 feller dommen (én kilde
    til formen, ikke to som kan drifte fra hverandre)."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, list) or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not verdi and not tom_ok:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not all(isinstance(e, dict) for e in verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


#: DE FEILFORMEDE KROPPENE, felt av CHECK-ene og NOT NULL-ene i 094.
#:
#: Dette er ikke en tilstandskonflikt, og det var CodeRabbits funn på
#: første runde: `_liste` validerer BEVISST ikke komponentformen selv
#: (én kilde til formen, ikke to som kan drifte fra hverandre), så
#: `malkomponent_form_total` og `malfelt.paakrevd NOT NULL` ER
#: forespørselsvalideringen. Et 409 på dem ville sagt «malens tilstand
#: sier nei» om en kropp som aldri var velformet — og etterlatt
#: forfatteren uten å vite at det var HENNES komponentliste som var gal.
_KROPPSDOMMER = (
    psycopg.errors.CheckViolation,
    psycopg.errors.NotNullViolation,
)


def _fra_dor(e) -> bool:
    """Kom `insufficient_privilege` fra en RAISE INNE i en vakt/dør, eller
    fra en manglende GRANT?

    Begge er SQLSTATE 42501, og forskjellen er hele forskjellen: det
    første er en DOM (vakten avviste), det andre er en DRIFTSFEIL (denne
    installasjonen mangler `migrer.py`-grantene). Uten skillet ville en
    halvferdig deploy svart «malens tilstand tillater ikke dette» på hver
    eneste skrivevei, mens sannheten var at rollen ikke hadde EXECUTE.

    Diskriminatoren er `CONTEXT`: en `RAISE` fra PL/pgSQL bærer alltid en
    kontekstlinje som navngir funksjonen, mens en rettighetsnektelse fra
    planleggeren ikke gjør det.
    """
    diag = getattr(e, "diag", None)
    return bool(diag is not None and getattr(diag, "context", None))


def _doerfeil(e, rid):
    """Dørens ERRCODE → flatens feilkode. ÉN kilde, så alle fem veiene
    svarer likt på samme dom. Returnerer `None` for alt som ikke er en
    dom — kalleren kaster da originalfeilen videre, og `_med_conn`
    svarer `db_utilgjengelig` med driftsloggen på.

    REKKEFØLGEN ER BÆRENDE: `UniqueViolation`, `CheckViolation` og
    `NotNullViolation` er alle underklasser av
    `IntegrityConstraintViolation`, så den generiske armen må stå SIST.
    """
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Kroppen er feilformet på en måte DØREN så og API-et ikke gjorde
        # (ukjent feltnøkkel, ulovlig verditype). 400, ikke 409: det er
        # forespørselen som er gal, ikke tilstanden.
        return _Avbrudd(_feil("request_feilformet", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, _KROPPSDOMMER):
        return _Avbrudd(_feil("request_feilformet", rid))
    if isinstance(e, psycopg.errors.InsufficientPrivilege):
        # Vakten sa nei → tilstandsdom. Manglende GRANT → driftsfeil.
        return (_Avbrudd(_feil("dokumentmal_ulovlig_tilstand", rid, 409))
                if _fra_dor(e) else None)
    if isinstance(e, psycopg.errors.IntegrityConstraintViolation):
        # 409: kroppen ER velformet — det er TILSTANDEN som sier nei
        # (allerede publisert, udeklarert felt, versjonen er ikke i
        # kraft), og forskjellen er hele forklaringen mennesket trenger.
        return _Avbrudd(_feil("dokumentmal_ulovlig_tilstand", rid, 409))
    return None


def _dor(conn, sql, args, rid):
    """Ett dørkall med den delte feiloversettelsen rundt."""
    try:
        return conn.execute(sql, args).fetchone()
    except psycopg.Error as e:
        avbrudd = _doerfeil(e, rid)
        if avbrudd is None:
            raise           # driftsfeil — rammen svarer db_utilgjengelig
        raise avbrudd from e


# ---------------------------------------------------------------------
# Skriveveiene — fire dører, ingen tabell røres direkte
# ---------------------------------------------------------------------

def familie_endepunkt(tjeneste, request):
    """POST /v1/dokumentmal/familier (bestilling:opprett, idem)."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        navn = _tekst(kropp, "navn", rid, 200)
        beskrivelse = _tekst(kropp, "beskrivelse", rid, 2000, paakrevd=False)
        fid = _utled(tenant, nokkel, "familie")
        _dor(conn, "SELECT m5_opprett_malfamilie(%s,%s,%s,%s,%s)",
             (tenant, navn, beskrivelse, _bid, fid), rid)
        conn.commit()
        return _ok({"familie_id": str(fid)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def versjon_endepunkt(tjeneste, request):
    """POST /v1/dokumentmal/versjoner (bestilling:opprett, idem).

    HELE utkastet i ett kall — komponentene OG feltdeklarasjonene. Det er
    dørens form, ikke API-ets bekvemmelighet: en versjon som kunne
    eksistere halvferdig mellom to HTTP-kall ville vært en mal noen kunne
    publisere med et hull i.
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
        try:
            familie_id = uuidlib.UUID(str(kropp.get("familie_id")))
        except (ValueError, TypeError):
            raise _Avbrudd(_feil("request_feilformet", rid))
        komponenter = _liste(kropp, "komponenter", rid, MAKS_KOMPONENTER,
                             tom_ok=False)
        felt = _liste(kropp, "felt", rid, MAKS_FELT, tom_ok=True)
        vid = _utled(tenant, nokkel, "versjon")
        rad = _dor(conn,
                   "SELECT ut_versjon_id, ut_versjonsnr FROM"
                   " m5_opprett_malversjon(%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
                   (tenant, familie_id, json.dumps(komponenter),
                    json.dumps(felt), _bid, vid), rid)
        conn.commit()
        return _ok({"versjon_id": str(rad[0]), "versjonsnr": rad[1]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def _overgang(tjeneste, request, dor: str, scope: str = "bestilling:opprett"):
    """Publiser og trekk tilbake er den SAMME formen — ett dørkall på en
    versjons-id — og deler derfor kropp. Dommen (er den et utkast? er den
    i kraft? refererer den et udeklarert felt?) felles av døren, aldri
    her: en flate som sjekket selv ville vært en andre sannhet å omgå."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _med_conn,
                                   _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid, scope)
        _krev_idem(request, rid)
        vid = _versjon_id(request, rid)
        rad = _dor(conn, f"SELECT {dor}(%s,%s,%s)", (tenant, vid, _bid), rid)
        conn.commit()
        return _ok({"versjon_id": str(vid), "versjonsnr": rad[0]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def publiser_endepunkt(tjeneste, request):
    """POST /v1/dokumentmal/versjon/{versjon_id}/publiser."""
    return _overgang(tjeneste, request, "m5_publiser_malversjon")


def trekk_tilbake_endepunkt(tjeneste, request):
    """POST /v1/dokumentmal/versjon/{versjon_id}/trekk-tilbake."""
    return _overgang(tjeneste, request, "m5_trekk_tilbake_malversjon")


# ---------------------------------------------------------------------
# Utfyllingen — den bærende veien
# ---------------------------------------------------------------------

def utfylling_endepunkt(tjeneste, request):
    """POST /v1/dokumentmal/versjon/{versjon_id}/utfylling (decisions:read).

    RETURNERER. Lagrer ikke, sender ikke, publiserer ikke.

    Svaret er en KOMPONENTLISTE og ikke én tekststreng — flaten skal
    kunne MARKERE hullene, og en streng er nettopp det stedet et hull
    forsvinner. `mangler` er den avledede listen over påkrevde felt uten
    dekning; den regnes HER av de samme radene flaten får, så de to kan
    ikke si ulike ting.

    INGEN `Idempotency-Key`: en idempotensnøkkel er et løfte om at et
    gjenspill ikke skaper noe nytt, og her finnes det ingenting å skape.
    Å kreve en ville vært å late som kallet er en skriving.

    Transaksjonen committes ALDRI. `_med_conn` gir forbindelsen tilbake
    til poolen, som ruller ubetinget tilbake — og `m5_fyll_mal` er
    dessuten STABLE, så det finnes ingen skriving å rulle.
    """
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "decisions:read")
        kropp = _kropp(request)
        vid = _versjon_id(request, rid)
        verdier = kropp.get("verdier", {})
        if not isinstance(verdier, dict) or len(verdier) > MAKS_VERDIER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        try:
            rader = conn.execute(
                "SELECT rekkefolge, komponenttype, feltnokkel, laast,"
                "       paakrevd, dekket, tekst"
                "  FROM m5_fyll_mal(%s,%s,%s::jsonb) ORDER BY rekkefolge",
                (tenant, vid, json.dumps(verdier))).fetchall()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise       # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        komponenter = [
            {"rekkefolge": r[0], "komponenttype": r[1], "feltnokkel": r[2],
             "laast": r[3], "paakrevd": r[4], "dekket": r[5],
             # `tekst` er `null` for et felt uten dekning — ALDRI en tom
             # streng, aldri feltnøkkelen, aldri en plassholder. Det er
             # hele modulens eksistensberettigelse, og det er derfor
             # verdien ikke normaliseres på vei ut heller.
             "tekst": r[6]}
            for r in rader]
        mangler = [k["feltnokkel"] for k in komponenter
                   if k["komponenttype"] == "felt" and k["paakrevd"]
                   and not k["dekket"]]
        return _ok({"versjon_id": str(vid), "komponenter": komponenter,
                    "mangler": mangler,
                    "fullstendig": not mangler}, rid)

    return _med_conn(tjeneste, rid, kjor)
