"""M-18 kunde-onboardingagentens API (migrasjon 103).

Ni endepunkter: tre leseveier og seks skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_onboarding_eier`-eid SECURITY DEFINER-dør i 103, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN PROVISJONERER INGENTING. Det er v1-dommen, og den er en EGENSKAP
VED DENNE FILEN og ikke bare en intensjon: her finnes ingen HTTP-klient,
ingen import av `ssrf`/`httpx`/`urllib`, ingen identitetsleverandør,
ingen kontooppretting og ingen utgående vei. Katalogen lover 0 minutter
per ny kunde; v1 registrerer LØPET. En automatisk provisjonering
forutsetter at man vet hva et FULLFØRT løp er — og en halvferdig kunde
er verre enn en uopprettet.

REGISTERET SPEILER IKKE M-12. Et steg kan NEVNE en tilgang i sin egen
tekst; det finnes ingen kolonne, ingen fremmednøkkel og ingen dør her
som sier noe om hvem som HAR den. To registre som begge påstår å vite
hvem som har hva, kan aldri holdes i takt.

SEKVENSREGELEN HÅNDHEVES IKKE HER. Et obligatorisk steg kan ikke stå som
fullført mens et lavere nummerert obligatorisk steg ikke er det — vakten
i 103 feller dommen, og et forsøk blir 409 fordi BASEN nektet. Flaten
viser `blokkert` fra lesedøren for at knappen ikke skal love noe
serveren avviser, men det er ergonomi, ikke sikkerhet.

SCOPENE ER GJENBRUKT, IKKE NYE.

  * LESINGEN bærer `decisions:read`. Et onboardingløp er tenantens
    alminnelige arbeidsflate — hvem som gjør hva for en ny kunde er
    ikke administratorens hemmelighet, og det er ingen persondata her
    utover et kundenavn og interne bruker-id-er.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som M-21
    (096), M-34 (100), M-13 (101) og M-17 (102).

SP-2 PÅ SKRIVEVEIENE SOM FØDER EN RAD: `mal_id` og `lop_id` utledes
deterministisk av Idempotency-Key-en. Et dobbelt startet løp ville gitt
to sett steg for den samme kunden, og «hvor står vi» to svar.
"""
from __future__ import annotations

import json
import uuid as uuidlib

import psycopg

#: Taket for hvor mange løp flaten viser. Dørens tak er 1000; dette er
#: flatens. SAMMENDRAGET TELLER LIKEVEL ALT.
MAKS_LOP = 200

#: Lengdegrensene på kundens egen tekst.
MAKS_NAVN = 200
MAKS_KUNDE_REF = 300
MAKS_BESKRIVELSE = 2000
MAKS_NOTAT = 4000
MAKS_BEGRUNNELSE = 2000

#: Ytterpunktene for et steg. Femti steg i ett løp er en sjekkliste som
#: har blitt en prosess ingen leser; 3650 døgn er ti år, altså ikke en
#: frist.
MAKS_STEG = 50
MAKS_FRIST_DOGN = 3650

#: SPEIL av CHECK-ene i 103.
AVSLUTNINGER = ("fullfort", "avbrutt")

_M18_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m18:onboarding")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M18_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valgfri_tekst(kropp, felt: str, rid, maks: int) -> str | None:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if not isinstance(verdi, str) or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi.strip() or None


def _uuid_felt(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, TypeError, AttributeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _stegliste(kropp, rid) -> str:
    """Malens stegliste, validert HER og serialisert til JSON for døren.

    VALIDERINGEN ER FLATENS, IKKE BASENS: døren tar imot en `jsonb` og
    lar CHECK-ene i §1 avvise det som ikke holder mål — men en liste med
    et manglende `navn` ville da blitt en `not_null_violation` uten et
    ord om hvilket steg som var galt. Her blir den 400 med en gang.

    `isinstance(x, bool)` på `frist_dogn`: i Python er `True` en `int`,
    og uten sjekken ville `{"frist_dogn": true}` blitt fristen 1 døgn.
    """
    from .policyadmin_http import _Avbrudd, _feil
    steg = kropp.get("steg")
    if not isinstance(steg, list) or not (1 <= len(steg) <= MAKS_STEG):
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for s in steg:
        if not isinstance(s, dict):
            raise _Avbrudd(_feil("request_feilformet", rid))
        navn = s.get("navn")
        beskrivelse = s.get("beskrivelse")
        frist = s.get("frist_dogn")
        obligatorisk = s.get("obligatorisk", True)
        if not isinstance(navn, str) or not navn.strip() \
                or len(navn) > MAKS_NAVN:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if not isinstance(beskrivelse, str) or not beskrivelse.strip() \
                or len(beskrivelse) > MAKS_BESKRIVELSE:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if not isinstance(frist, int) or isinstance(frist, bool) \
                or not (0 <= frist <= MAKS_FRIST_DOGN):
            raise _Avbrudd(_feil("request_feilformet", rid))
        if not isinstance(obligatorisk, bool):
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append({"navn": navn, "beskrivelse": beskrivelse,
                   "frist_dogn": frist, "obligatorisk": obligatorisk})
    return json.dumps(ut)


#: De ENESTE feilklassene dørene bruker som DOM.
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
        # Dørenes egne RAISE-er: en eier som ikke er aktivt medlem, et
        # avsluttet løp som får flere steg, et avbrutt løp uten
        # begrunnelse. Kroppen ER velformet — det er innholdskravet
        # basen håndhever som sier nei.
        return _Avbrudd(_feil("onboarding_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # SEKVENSREGELEN LANDER HER, via vaktens
        # `insufficient_privilege`: et steg som ble forsøkt fullført før
        # sitt forgjengersteg. Det er en TILSTAND som sier nei, ikke en
        # feilformet kropp — og 409 er derfor det ærlige svaret.
        return _Avbrudd(_feil("onboarding_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Onboardingflatens tilstand i én transaksjon, gjennom tre lesedører.

    STEGENE FØLGER IKKE MED I LISTEN. De hentes per løp når et menneske
    åpner det — et listekall som dro med seg hvert steg i hvert løp ville
    vært n+1 rader for en flate som viser én linje per løp.
    """
    s = conn.execute("SELECT * FROM m18_onboardingstatus(%s)",
                     (tenant,)).fetchone()
    lop = [
        {"lop_id": str(r[0]), "kunde_ref": r[1], "mal_navn": r[2],
         "mal_versjon": r[3], "startet": r[4].isoformat(), "status": r[5],
         "eier_bruker_id": r[6], "eier_navn": r[7], "eier_aktiv": r[8],
         "alder_dogn": r[9], "gjort": r[10], "totalt": r[11],
         "obligatoriske_igjen": r[12], "neste_steg": r[13],
         "apne_funn": list(r[14] or ())}
        for r in conn.execute("SELECT * FROM m18_lopene(%s,%s)",
                              (tenant, MAKS_LOP)).fetchall()]
    maler = [
        {"mal_id": str(r[0]), "navn": r[1], "versjon": r[2],
         "aktiv": r[3], "antall_steg": r[4], "paagaende_lop": r[5]}
        for r in conn.execute("SELECT * FROM m18_malene(%s)",
                              (tenant,)).fetchall()]
    return {
        "sammendrag": {
            "paagaende": s[0], "fullforte": s[1], "avbrutte": s[2],
            "stoppede": s[3], "apne_funn": s[4], "maler": s[5],
            # LISTEN ER AVKORTET, OG FLATEN SKAL KUNNE SI DET.
            "vist": len(lop)},
        "lop": lop, "maler": maler}


def onboardingbilde(tjeneste, request):
    """GET /v1/onboarding (decisions:read) — tenantens egne løp."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


def stegene_endepunkt(tjeneste, request):
    """GET /v1/onboarding/lop/{lop_id}/steg (decisions:read).

    `dogn_over_frist` og `blokkert` er regnet i BASEN, i samme skann som
    raden (M-16-regelen) — flaten skal verken trekke to datoer fra
    hverandre eller løpe gjennom listen for å finne ut om et steg kan
    gjøres.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        lid = _sti_uuid(request, "lop_id", rid)
        rader = conn.execute("SELECT * FROM m18_stegene(%s,%s)",
                             (auth.tenant, lid)).fetchall()
        steg = [
            {"steg_nr": r[0], "navn": r[1], "beskrivelse": r[2],
             "frist_dogn": r[3], "obligatorisk": r[4],
             "eier_bruker_id": r[5], "eier_navn": r[6],
             "fullfort_ts": r[7].isoformat() if r[7] is not None else None,
             "fullfort_av": r[8], "notat": r[9],
             "forfaller": r[10].isoformat(),
             "dogn_over_frist": r[11], "blokkert": r[12]}
            for r in rader]
        return kanonisk_json({"lop_id": str(lid), "steg": steg,
                              "request_id": rid},
                             200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


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
        sql, args, svar = bygg(tenant, bid, nokkel, kropp, rid, request)
        try:
            ut = conn.execute(sql, args).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            # Datoen er kallerens tekst, og castet skjer i basen. En
            # ulesbar dato er 400, ikke 409. Fanget som to navngitte
            # klasser og ikke som `DataError`, fordi `DataError` også
            # dekker 22023 — dørenes egen dom, som skal bli 409.
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
        return _ok({**svar, "versjon": int(ut)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def mal_endepunkt(tjeneste, request):
    """POST /v1/onboarding/mal (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        mid = _utled("mal", tenant, nokkel)
        return ("SELECT m18_registrer_mal(%s,%s,%s,%s)",
                (tenant, mid, navn, bid), {"mal_id": str(mid)})
    return _skriv(tjeneste, request, bygg)


def malsteg_endepunkt(tjeneste, request):
    """POST /v1/onboarding/mal/{mal_id}/steg (bestilling:opprett, idem).

    HELE STEGSETTET I ETT KALL. En dør som la til ett steg om gangen
    ville latt malen stå i en halvferdig tilstand mellom kallene, og et
    løp startet i det vinduet ville fått et ufullstendig snapshot.
    Versjonen øker, så eldre løp beholder formen de ble startet på.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        mid = _sti_uuid(request, "mal_id", rid)
        steg = _stegliste(kropp, rid)
        return ("SELECT m18_sett_malsteg(%s,%s,%s::jsonb,%s)",
                (tenant, mid, steg, bid), {"mal_id": str(mid)})
    return _skriv(tjeneste, request, bygg)


def start_endepunkt(tjeneste, request):
    """POST /v1/onboarding/lop (bestilling:opprett, idem).

    EIEREN ER PÅKREVD I KROPPEN, ikke utledet av innloggingen. Den som
    skriver ned et løp er ofte ikke den som skal eie det, og en flate som
    stille satte innloggeren som eier ville gjort «løp uten eier» sann på
    papiret og falsk i praksis. Døren avviser en eier som ikke er aktivt
    medlem av tenanten.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        mid = _uuid_felt(kropp, "mal_id", rid)
        kunde = _tekst(kropp, "kunde_ref", rid, MAKS_KUNDE_REF)
        eier = _tekst(kropp, "eier_bruker_id", rid, 128)
        startet = _tekst(kropp, "startet", rid, 32)
        lid = _utled("lop", tenant, nokkel)
        return ("SELECT m18_start_lop(%s,%s,%s,%s,%s,%s::date,%s)",
                (tenant, lid, mid, kunde, eier, startet, bid),
                {"lop_id": str(lid)})
    return _skriv(tjeneste, request, bygg)


def stegeier_endepunkt(tjeneste, request):
    """POST /v1/onboarding/lop/{lop_id}/steg/{steg_nr}/eier
    (bestilling:opprett, idem).

    Det ENE som lovlig flyttes på et steg utenom fullføringen: hvem som
    skal gjøre det. Stegeierne arves fra løpets eier ved start og flyttes
    deretter ett og ett — å kreve alle eierne opp front ville gjort det
    umulig å starte et løp før man visste hvem som skulle gjøre steg fem.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        lid = _sti_uuid(request, "lop_id", rid)
        try:
            nr = int(request.path_params.get("steg_nr"))
        except (TypeError, ValueError):
            raise _Avbrudd(_feil("request_feilformet", rid))
        eier = _tekst(kropp, "eier_bruker_id", rid, 128)
        return ("SELECT m18_sett_stegeier(%s,%s,%s,%s,%s)",
                (tenant, lid, nr, eier, bid),
                {"lop_id": str(lid), "steg_nr": nr})
    return _skriv(tjeneste, request, bygg)


def fullfor_endepunkt(tjeneste, request):
    """POST /v1/onboarding/lop/{lop_id}/steg/{steg_nr}/fullfor
    (bestilling:opprett, idem).

    SEKVENSREGELEN SJEKKES IKKE HER. Vakten i 103 feller dommen, og et
    forsøk blir 409 fordi BASEN nektet — ikke fordi API-et sjekket. Det
    er hele grunnen til at regelen bor i basen: den gjelder da også for
    direkte DML.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        lid = _sti_uuid(request, "lop_id", rid)
        try:
            nr = int(request.path_params.get("steg_nr"))
        except (TypeError, ValueError):
            raise _Avbrudd(_feil("request_feilformet", rid))
        notat = _valgfri_tekst(kropp, "notat", rid, MAKS_NOTAT)
        return ("SELECT m18_fullfor_steg(%s,%s,%s,%s,%s)",
                (tenant, lid, nr, notat, bid),
                {"lop_id": str(lid), "steg_nr": nr})
    return _skriv(tjeneste, request, bygg)


def avslutt_endepunkt(tjeneste, request):
    """POST /v1/onboarding/lop/{lop_id}/avslutt (bestilling:opprett,
    idem).

    «Fullført» krever at de obligatoriske stegene faktisk er gjort
    (vakten); «avbrutt» krever en begrunnelse (døren og CHECK-en). Ingen
    av delene sjekkes her.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        lid = _sti_uuid(request, "lop_id", rid)
        status = kropp.get("status")
        if status not in AVSLUTNINGER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        begrunnelse = _valgfri_tekst(kropp, "begrunnelse", rid,
                                     MAKS_BEGRUNNELSE)
        return ("SELECT m18_avslutt_lop(%s,%s,%s,%s,%s)",
                (tenant, lid, status, begrunnelse, bid),
                {"lop_id": str(lid), "status": status})
    return _skriv(tjeneste, request, bygg)
