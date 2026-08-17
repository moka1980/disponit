"""Modul-onboarding over HTTP (035): hemmelighet → token → rotasjon.

To faser (klarsignalet §5): mennesket med `modules:onboard` får en
ENGANGSHEMMELIGHET (60 min, hashet, vist én gang); deploymenten bytter den
i sitt langlivede token ved oppstart. Tokenet skal aldri passere et
menneske, en config-fil eller en deploy-logg — derfor er innløsningen
maskinens eget kall, autentisert av selve hemmeligheten.

LUKKEDE SKJEMAER hele veien (portene 7–8): en request som sender
identitetsfelt endepunktet selv slår opp (modul/release/miljø/epoch ved
innløsning og rotasjon) avvises — identiteten kommer fra RADEN og TOKENET,
aldri fra requesten.

Wire-formater (vist én gang, aldri lagret i klartekst):
  hemmelighet:  ``onb_<onboarding_id>.<secret>``
  modultoken:   ``mtk_<token_id>.<secret>``
Begge verifiseres som pepper-MAC (samme mekanisme som `api_tokener`).
"""
from __future__ import annotations

import json
import secrets
import uuid

import psycopg
from starlette.requests import Request
from starlette.responses import Response

#: Serverkonfigurerte frister (klarsignalet §5) — aldri fra requesten.
FAMILIE_DAGER = 365
TOKEN_DAGER = 30
HEMMELIGHET_TTL_MIN = 60

#: Budsjett for HELE den uautentiserte innløsningsruten, per minutt og per
#: prosess (Codex P1). Det er ikke prosessens standardgrense: den er satt av
#: ytelsesporten (12 000/min) for beslutningsveien, og innløsning er ingen
#: varm sløyfe — den skjer én gang per utrulling. Grensen skal derfor kunne
#: være liten nok til å bety noe for en flate der kalleren selv velger
#: nøkkelen sin.
INNLOS_RATE_PER_MIN = 60

ONBOARD_SCOPE = "modules:onboard"


def _kropp(request: Request, *, tillatt: frozenset[str]) -> dict | None:
    """Lukket skjema: KUN nøklene i `tillatt`. -> None ved brudd."""
    raa = request.scope.get("state", {}).get("kropp", b"")
    try:
        data = json.loads(raa.decode("utf-8")) if raa else {}
    except (ValueError, RecursionError):
        # Codex P2: `json.loads` er REKURSIV. Et syntaktisk gyldig, dypt
        # nøstet dokument på noen få kilobyte (≈2 000 nivåer) ligger godt
        # under kroppsgrensen på 256 KiB og treffer likevel
        # rekursjonsgrensen — RecursionError er en RuntimeError, ikke en
        # ValueError. Uten den her slapp dybden ut som generisk 500 på alle
        # onboarding-rutene, og på den UAUTENTISERTE `/innlos` var det en
        # gratis feil-/tilgjengelighetsflate. Dybde er klientinput; svaret
        # er det dokumenterte `request_feilformet`, som i artefaktparseren.
        return None
    if not isinstance(data, dict) or set(data) - tillatt:
        return None
    return data


def utsted_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/modul/onboarding — fase 1. Krever `modules:onboard` på et
    Bearer-token; vilkårene (claiming, aktiv/staging_verifisert, registrert
    oppdragstype) er MASKINVERIFISERTE i `utsted_onboarding_hemmelighet` —
    scopet gir retten til å forsøke, ikke retten til å bestemme."""
    from .app import _rid, _feilsvar, preauth, kanonisk_json, _mac
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        auth = preauth(tjeneste, conn, request.headers.get("authorization"),
                       rid)
        if auth is None or auth.kapabilitet is not None \
                or getattr(auth, "modul_id", None) is not None:
            # Verken kapabiliteter eller MODULTOKENER kan onboarde nye
            # moduler — et stjålet modultoken skal ikke kunne formere seg.
            tjeneste.logg.hendelse("token_ugyldig", rid)
            return _feilsvar("token_ugyldig", rid)
        if ONBOARD_SCOPE not in auth.scopes:
            tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                   scope=ONBOARD_SCOPE)
            return _feilsvar("scope_mangler", rid)
        data = _kropp(request,
                      tillatt=frozenset({"modul_id", "miljo", "release_id"}))
        if data is None or not all(
                isinstance(data.get(k), str) and data.get(k)
                for k in ("modul_id", "miljo", "release_id")):
            return _feilsvar("request_feilformet", rid)
        oid = uuid.uuid4()
        hemmelighet = secrets.token_hex(32)
        try:
            rad = conn.execute(
                "SELECT * FROM utsted_onboarding_hemmelighet("
                "%s,%s,%s,%s,%s,%s,%s,%s)",
                (data["modul_id"], data["miljo"], data["release_id"], oid,
                 _mac(tjeneste.pepper, hemmelighet), FAMILIE_DAGER,
                 HEMMELIGHET_TTL_MIN, auth.aktor)).fetchone()
            conn.commit()
        except (psycopg.errors.NoDataFound,
                psycopg.errors.InvalidParameterValue,
                psycopg.errors.UniqueViolation) as e:
            conn.rollback()
            # Operatøren er autentisert og autorisert — vilkåret som feilet
            # SKAL forklares (dette er ingen orakelflate): det står i loggen
            # og i svaret som lukket kode + diagnostisk tekst.
            tjeneste.logg.hendelse("onboarding_vilkaar", rid, auth.tenant,
                                   detalj=str(e).split("\n")[0][:160])
            return kanonisk_json(
                {"feil": "request_feilformet",
                 "detalj": str(e).split("\n")[0][:160],
                 "request_id": rid}, 409, {"x-request-id": rid})
        # Hemmeligheten vises ÉN gang. Den finnes ellers bare som MAC.
        return kanonisk_json({
            "onboarding_id": str(oid),
            "hemmelighet": f"onb_{oid}.{hemmelighet}",
            "utloper": rad[1].isoformat(),
            "familie_utloper": rad[2].isoformat(),
            "request_id": rid}, 201, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)


def innlos_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/modul/onboarding/innlos — fase 2. Hemmeligheten ER
    autentiseringen; kroppen er LUKKET til nøyaktig {hemmelighet}: en
    request som sender modul_id/release_id avvises (port 7 — identiteten
    kommer fra raden). Alle avvisningsgrunner er samme svar (intet orakel)."""
    from .app import _rid, _feilsvar, kanonisk_json, _mac
    rid = _rid(request)
    data = _kropp(request, tillatt=frozenset({"hemmelighet"}))
    if data is None or not isinstance(data.get("hemmelighet"), str):
        return _feilsvar("request_feilformet", rid)
    raa = data["hemmelighet"]
    if not raa.startswith("onb_") or "." not in raa:
        return _feilsvar("onboarding_avvist", rid)
    oid_del, _, secret = raa[4:].partition(".")
    try:
        oid = uuid.UUID(oid_del)
    except ValueError:
        return _feilsvar("onboarding_avvist", rid)
    # RATEN FØR TILKOBLINGEN, OG RUTEN FØR ID-EN (Codex P1).
    #
    # To feil i én linje. Nøkkelen var utelukkende onboarding-id-en fra
    # kroppen — altså KALLERENS EGEN INPUT: en angriper som sender en fersk,
    # gyldig UUID i hver request treffer aldri samme bøtte, slipper alltid
    # gjennom, og trenger verken hemmelighet eller kjent id for å holde
    # `innlos_onboarding` i gang mot Postgres i det uendelige. Og siden
    # sjekken sto ETTER `pool.hent()`, hadde hver slik request alt tatt en
    # pool-tilkobling — nettopp ressursen grensen finnes for å verne.
    #
    # Per-id-grensen BLIR STÅENDE: den er brute-force-vernet for et stjålet
    # id-ledd (hemmeligheten er 256 bit, men et fritt antall forsøk er
    # fortsatt et gratis orakel). Over den ligger et budsjett for HELE ruten,
    # som ingen valgt id kan gå utenom. Rutebudsjettet sjekkes først, så en
    # id-flom ikke kan fylle nøkkelrommet på veien.
    #
    # Ærlig om kostnaden: et delt budsjett betyr at støy på ruten kan
    # forsinke en LEGITIM innløsning. Det er en akseptert bytte her —
    # innløsning skjer ved utrulling, ikke i en varm sløyfe, og alternativet
    # er ubegrenset databasetrafikk fra en uautentisert flate.
    if not tjeneste.rate.slipp_gjennom("onb:innlos", tak=INNLOS_RATE_PER_MIN) \
            or not tjeneste.rate.slipp_gjennom(f"onb:{oid}"):
        tjeneste.logg.hendelse("rate_grense", rid)
        return _feilsvar("rate_grense", rid)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        tid = uuid.uuid4()
        token_secret = secrets.token_hex(32)
        try:
            rad = conn.execute(
                "SELECT * FROM innlos_onboarding(%s,%s,%s,%s,%s,%s)",
                (oid, _mac(tjeneste.pepper, secret), tid,
                 _mac(tjeneste.pepper, token_secret), TOKEN_DAGER,
                 f"innlosning:{oid}")).fetchone()
            # Codex P2: en AVVISNING committes, den rulles ikke tilbake.
            # Funksjonen har da skrevet `avvist_bruk` i det append-only
            # sporet og returnert `avvist` satt i stedet for å raise — et
            # RAISE ville rullet nettopp den hendelsen bort igjen, og
            # sporet ville aldri sett et eneste mislykket forsøk. Intet
            # token er opprettet og hemmeligheten er urørt, så det eneste
            # som committes er revisjonsraden. Kvoten på forsøk (og altså
            # på rader) er rate-grensen over.
            avvist = rad is None or rad[7] is not None
            conn.commit()
            if avvist:
                tjeneste.logg.hendelse("onboarding_avvist", rid)
                return _feilsvar("onboarding_avvist", rid)
        except (psycopg.errors.NoDataFound,
                psycopg.errors.InvalidParameterValue):
            # Ukjent onboarding-id: ingen rad å tilskrive en hendelse.
            conn.rollback()
            tjeneste.logg.hendelse("onboarding_avvist", rid)
            return _feilsvar("onboarding_avvist", rid)
        # Tokenet vises ÉN gang — det passerer aldri et menneske eller en
        # deploy-logg; deploymenten holder det i minnet og roterer selv.
        return kanonisk_json({
            "token": f"mtk_{tid}.{token_secret}",
            "modul_id": rad[1], "miljo": rad[2], "release_id": rad[3],
            "utloper": rad[5].isoformat(),
            "familie_utloper": rad[6].isoformat(),
            "request_id": rid}, 201, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)


def roter_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/modul/token/roter — selvrotasjon, autentisert av tokenet
    selv. Kroppen er lukket til nøyaktig ÉN valgfri nøkkel: `rotasjon_id`.
    Levetid og frister er fortsatt serverens (port 8-formen) — nøkkelen er
    ingen parameter til rotasjonen, den IDENTIFISERER FORSØKET.

    HVORFOR NØKKELEN FINNES (Codex P1): etterfølgerens hemmelighet mynter
    serveren og viser den én gang. Går 201-svaret tapt på veien hjem — en
    tidsavbrutt forbindelse, en død proxy — holder INGEN etterfølgeren,
    men raden opptar forgjengerens eneste etterfølgerplass. Uten en
    idempotensnøkkel var det gjentatte forsøket umulig å skille fra en ekte
    konflikt, og modulen var ute av drift 15 minutter senere, til et
    menneske onboardet den på nytt. Deploymenten skal derfor generere
    `rotasjon_id` ÉN gang per rotasjon og sende SAMME verdi i hvert forsøk;
    da mynter serveren neste forsøk i SAMME rotasjon. Uten nøkkel er svaret
    409, som før.

    Og forsøkene TAR IKKE LIVET AV HVERANDRE (Codex P1, runde 3): serveren
    vet ikke om det forrige svaret gikk tapt eller bare var forsinket, så
    et forsøk som tilbakekalte forgjengerens forrige etterfølger kunne
    drepe nettopp den hemmeligheten deploymenten hadde lagret. Alle
    forsøkene i én rotasjon lever; deploymenten bruker den den fikk. Taket
    er fem forsøk, og over det er svaret 409."""
    from .app import _rid, _feilsvar, preauth, kanonisk_json, _mac
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        auth = preauth(tjeneste, conn, request.headers.get("authorization"),
                       rid)
        if auth is None or getattr(auth, "modul_id", None) is None:
            tjeneste.logg.hendelse("token_ugyldig", rid)
            return _feilsvar("token_ugyldig", rid)
        if not tjeneste.rate.slipp_gjennom(auth.token_id):
            return _feilsvar("rate_grense", rid)
        data = _kropp(request, tillatt=frozenset({"rotasjon_id"}))
        if data is None:
            return _feilsvar("request_feilformet", rid)
        rotasjon_id = None
        if "rotasjon_id" in data:
            try:
                rotasjon_id = uuid.UUID(str(data["rotasjon_id"]))
            except ValueError:
                return _feilsvar("request_feilformet", rid)
        ny_id = uuid.uuid4()
        ny_secret = secrets.token_hex(32)
        try:
            rad = conn.execute(
                "SELECT * FROM roter_modultoken(%s,%s,%s,%s,%s,%s)",
                (auth.modultoken_id, ny_id,
                 _mac(tjeneste.pepper, ny_secret), TOKEN_DAGER,
                 f"modul:{auth.modul_id}", rotasjon_id)).fetchone()
            conn.commit()
        except psycopg.errors.UniqueViolation:
            # To ULIKE rotasjoner: lagringen lot én vinne. Taperen har
            # fortsatt sitt gamle token i nådevinduet og kan hente
            # etterfølgeren der den ble levert — dette er en konflikt, ikke
            # en feil hos serveren. Det GJENTATTE forsøket (samme
            # `rotasjon_id`) havner her først når taket på fem forsøk er
            # nådd; da er dette ikke lenger en tapt pakke.
            conn.rollback()
            return _feilsvar("onboarding_avvist", rid, 409)
        except psycopg.errors.InvalidParameterValue:
            conn.rollback()
            return _feilsvar("onboarding_avvist", rid)
        # Codex P2: den FAKTISKE fristen, ikke påstanden «15 minutter».
        # Nåden er et kvarter fra nå bare når forgjengeren var urørt: sto
        # den alt i et nådevindu fra en tidligere hendelse, gjelder den
        # gamle fristen, og et gjentatt forsøk sent i nåden arver den samme.
        # Verifikasjonen håndhever dessuten `utloper`, så en rotasjon rett
        # før tokenets egen utløp — eller mot familiehorisonten — gir
        # sekunder, ikke et kvarter. En klient som planla overlappende
        # overlevering på et kvarter som ikke fantes, fikk overleveringen
        # kuttet midt i. Serveren regner fristen under låsen og sier den.
        return kanonisk_json({
            "token": f"mtk_{ny_id}.{ny_secret}",
            "utloper": rad[1].isoformat(),
            "familie_utloper": rad[2].isoformat(),
            "forgjenger_gyldig_til": rad[3].isoformat(),
            "request_id": rid}, 201, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)


def tilbakekall_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/modul/token/tilbakekall — `modules:onboard`, umiddelbar,
    auditert grunn."""
    from .app import _rid, _feilsvar, preauth, kanonisk_json
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        auth = preauth(tjeneste, conn, request.headers.get("authorization"),
                       rid)
        if auth is None or auth.kapabilitet is not None \
                or getattr(auth, "modul_id", None) is not None:
            tjeneste.logg.hendelse("token_ugyldig", rid)
            return _feilsvar("token_ugyldig", rid)
        if ONBOARD_SCOPE not in auth.scopes:
            tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                   scope=ONBOARD_SCOPE)
            return _feilsvar("scope_mangler", rid)
        data = _kropp(request, tillatt=frozenset({"token_id", "grunn"}))
        if data is None or not isinstance(data.get("grunn"), str) \
                or not data["grunn"].strip():
            return _feilsvar("request_feilformet", rid)
        try:
            tid = uuid.UUID(str(data.get("token_id")))
        except ValueError:
            return _feilsvar("request_feilformet", rid)
        try:
            conn.execute("SELECT tilbakekall_modultoken(%s,%s,%s)",
                         (tid, data["grunn"], auth.aktor))
            conn.commit()
        except psycopg.errors.NoDataFound:
            conn.rollback()
            return _feilsvar("ikke_funnet", rid)
        return kanonisk_json({"tilbakekalt": str(tid), "request_id": rid},
                             200, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)
