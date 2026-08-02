"""Nettverksinngangen (v2 Del 3-4, v3-delta, PR-005b korreksjon 1-3).

Designregelen fra 005b-spesifikasjonen gjelder hver eneste port her: den
bygges, gjøres obligatorisk og gjøres umulig å omgå i SAMME leveranse.
Ingen av portene under er konfigurerbar middleware man kan koble fra —
`lag_app()` er eneste vei til en app, og den nekter å returnere en app
hvis noen av boot-sjekkene feiler. Endepunktet finnes ikke uten dem.

Loopback-bindingen er kodet, ikke skrevet: `krev_lovlig_bind()` avviser
enhver adresse utenfor loopback med mindre DISPONIT_TLS_AKTIV er satt.
En instruks i et dokument om at man «ikke skal eksponere API-et» er ingen
port — dette er.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import queue
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from db import kryptering
from db.pg import koble, sett_kontekst
from policy_validator.attestering import last_nokler
from policy_validator.engine import EvaluationContext

from . import cursor as cursormodul
from . import feil as feiltabell
from . import kjerne

MAKS_KROPP = 256 * 1024          # v2 Del 3.4

#: Ytelsesporten perf-m01-v1 krever 100 beslutninger/sekund vedvarende fra
#: ÉN klient. Standard rate-grense må derfor ligge over 6 000/minutt, ellers
#: gjør plattformen sitt eget ytelseskrav uoppnåelig.
#:
#: Oppdaget ved å KJØRE lasttesten: med den opprinnelige verdien 600/min
#: (= 10/s) fikk 5 400 av 6 000 forespørsler 429, og artefaktet rapporterte
#: en ytelsesfeil som i virkeligheten var en konfigurasjonsmotsigelse.
#: `test_rategrensen_star_ikke_i_veien_for_ytelsesporten` binder de to
#: tallene sammen så de ikke kan gli fra hverandre igjen.
YTELSESKRAV_PER_SEK = 100
STANDARD_RATE_PER_MIN = 2 * 60 * YTELSESKRAV_PER_SEK      # 12 000/min
SIDE_STANDARD, SIDE_MAKS = 50, 200
MIGRASJONSMAPPE = Path(__file__).resolve().parents[1] / "db" / "migrations"


class BootNekt(RuntimeError):
    """Prosessen skal ikke starte. Aldri en advarsel — alltid en stopp."""


# ---------------------------------------------------------------------------
# Sikkerhetslogg og metrics
# ---------------------------------------------------------------------------

class Sikkerhetslogg:
    """Strukturert logg + tellere. ALDRI payload, ALDRI tokens.

    v2 Del 4: sikkerhetsrouting i PR-005 er logg + metric; egen tabell er
    PR-006. Aggregerte rader (rate, body, feilformet) telles bare — én
    linje per treff er nettopp den kø-flommen vernet skal hindre.
    """

    def __init__(self, ut=None) -> None:
        self._ut = ut or sys.stderr
        self._laas = threading.Lock()
        self.tellere: dict[str, int] = {}
        self.linjer: list[dict] = []

    def hendelse(self, kode: str, request_id: str, tenant: str | None = None,
                 art: str = "sikkerhet", **ekstra) -> None:
        rad = feiltabell.FEIL.get(kode)
        with self._laas:
            self.tellere[kode] = self.tellere.get(kode, 0) + 1
            antall = self.tellere[kode]
        if rad is not None and rad.aggregert and antall > 1:
            return          # telleren står; linjen skrives kun første gang
        post = {"ts": datetime.now(timezone.utc).isoformat(), "art": art,
                "kode": kode, "request_id": request_id, "tenant": tenant,
                **ekstra}
        with self._laas:
            self.linjer.append(post)
        print(json.dumps(post, ensure_ascii=False), file=self._ut, flush=True)


# ---------------------------------------------------------------------------
# Tilkoblingspool
# ---------------------------------------------------------------------------

class Tilkoblingspool:
    """Liten, trådsikker pool. Sync med vilje.

    Endepunktene er vanlige `def`-funksjoner, ikke `async def`: Starlette
    kjører dem i en trådpool, og psycopg er en blokkerende driver. Hadde de
    vært `async def`, ville hver databaseventing blokkert hele
    event-loopen og lasttesten målt Python framfor plattformen.
    """

    def __init__(self, dsn: str, storrelse: int = 20) -> None:
        self._dsn = dsn
        self._ledige: queue.LifoQueue = queue.LifoQueue()
        self._laget = 0
        self._maks = storrelse
        self._laas = threading.Lock()

    def _ny(self) -> psycopg.Connection:
        return koble(self._dsn)

    def hent(self, timeout: float = 5.0) -> psycopg.Connection:
        try:
            conn = self._ledige.get_nowait()
            if conn.closed:
                conn = self._ny()
            return conn
        except queue.Empty:
            pass
        with self._laas:
            if self._laget < self._maks:
                self._laget += 1
                lag_ny = True
            else:
                lag_ny = False
        if lag_ny:
            try:
                return self._ny()
            except Exception:
                with self._laas:
                    self._laget -= 1
                raise
        try:
            return self._ledige.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("tilkoblingspoolen er tom") from None

    def gi_tilbake(self, conn: psycopg.Connection) -> None:
        try:
            if conn.closed:
                with self._laas:
                    self._laget -= 1
                return
            # Tilbake til poolen KUN i ren tilstand. En tilkobling med en
            # åpen transaksjon ville tatt med seg både SET LOCAL-verdier og
            # låser inn i neste forespørsel — altså neste tenants kontekst.
            conn.rollback()
            self._ledige.put(conn)
        except Exception:
            with self._laas:
                self._laget -= 1
            try:
                conn.close()
            except Exception:
                pass

    def lukk(self) -> None:
        while True:
            try:
                self._ledige.get_nowait().close()
            except queue.Empty:
                return
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Rate-grense (i minne, per prosess — deklarert svakhet, v2 Del 4)
# ---------------------------------------------------------------------------

class Rategrense:
    """Nullstilles ved restart og deles ikke mellom prosesser.

    Det er en kjent og akseptert svakhet i PR-005, ikke en forglemmelse:
    kompensasjonen er at API-et er loopback-only (boot-sperren), og M-38
    overtar med delt tilstand før ekstern eksponering. Den står her i koden
    og ikke bare i spesifikasjonen, slik at den som senere åpner porten
    faktisk leser den.
    """

    def __init__(self, per_minutt: int) -> None:
        self.per_minutt = per_minutt
        self._treff: dict[str, list[float]] = {}
        self._laas = threading.Lock()

    def slipp_gjennom(self, nokkel: str, naa: float | None = None) -> bool:
        naa = naa if naa is not None else time.monotonic()
        with self._laas:
            tider = [t for t in self._treff.get(nokkel, ()) if t > naa - 60.0]
            if len(tider) >= self.per_minutt:
                self._treff[nokkel] = tider
                return False
            tider.append(naa)
            self._treff[nokkel] = tider
            return True


# ---------------------------------------------------------------------------
# Boot-sjekker
# ---------------------------------------------------------------------------

def forventede_migrasjoner() -> list[int]:
    return sorted(int(f.name[:3])
                  for f in MIGRASJONSMAPPE.glob("[0-9][0-9][0-9]_*.sql"))


def er_loopback(vert: str) -> bool:
    if vert in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(vert).is_loopback
    except ValueError:
        return False


def krev_lovlig_bind(vert: str) -> None:
    """v2 Del 3.1: binding utenfor loopback er FORBUDT uten TLS-flagget.

    Kodet oppstartssjekk, ikke en instruks. Uten den er «API-et er
    loopback-only» en påstand som holder helt til noen skriver 0.0.0.0 i en
    systemd-fil, og da faller også begrunnelsen for at rate-grensen får
    være prosesslokal.
    """
    if er_loopback(vert):
        return
    if os.environ.get("DISPONIT_TLS_AKTIV") == "1":
        return
    raise BootNekt(
        f"bind-adresse {vert!r} er ikke loopback og DISPONIT_TLS_AKTIV != 1 —"
        " ekstern eksponering krever TLS (v2 Del 3.1)")


def krev_pepper() -> str:
    pepper = os.environ.get("DISPONIT_TOKEN_PEPPER", "")
    if len(pepper) < 32:
        raise BootNekt(
            "DISPONIT_TOKEN_PEPPER mangler eller er kortere enn 32 tegn —"
            " uten pepper er secret_mac i databasen nok til å lage tokens")
    return pepper


def cursorpepper(tokenpepper: str) -> str:
    """Egen nøkkel for cursorsignering, avledet av tokenpepperet.

    Samme hemmelighet til to formål er en kjent felle: en orakel-lekkasje i
    det ene bruket blir da en lekkasje i det andre. Avledningen gir to
    uavhengige nøkler fra én hemmelighet å administrere.
    """
    return hmac.new(tokenpepper.encode("utf-8"), b"disponit:cursor:v1",
                    hashlib.sha256).hexdigest()


def krev_migrasjonstilstand(conn: psycopg.Connection) -> list[int]:
    forventet = forventede_migrasjoner()
    faktisk = [r[0] for r in conn.execute(
        "SELECT versjon FROM migrasjoner ORDER BY versjon").fetchall()]
    conn.rollback()
    if faktisk != forventet:
        raise BootNekt(
            f"migrasjonstilstanden er {faktisk}, forventet {forventet} —"
            " kjør deploy/staging/migrer.py før API-et startes")
    return faktisk


# ---------------------------------------------------------------------------
# Body-grense: teller FAKTISK mottatte bytes
# ---------------------------------------------------------------------------

class KroppsgrenseMiddleware:
    """Ren ASGI, med vilje.

    Content-Length er en PÅSTAND fra klienten. En avsender som lyver, eller
    som bruker chunked transfer og dermed ikke oppgir lengde i det hele
    tatt, slipper forbi enhver kontroll som bare leser headeren. Derfor
    telles bytene som faktisk kommer inn, og forbindelsen avvises i det
    grensen passeres — før noe forsøkes tolket som JSON.
    """

    def __init__(self, app, maks: int = MAKS_KROPP, logg=None) -> None:
        self.app = app
        self.maks = maks
        self.logg = logg

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD"):
            return await self.app(scope, receive, send)

        headere = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        rid = scope.setdefault("state", {}).get("request_id") or _nytt_request_id()
        scope["state"]["request_id"] = rid

        oppgitt = headere.get("content-length")
        chunked = "chunked" in headere.get("transfer-encoding", "").lower()
        if oppgitt is None and not chunked:
            return await self._avvis(send, "body_lengde_ugyldig", rid)
        if oppgitt is not None:
            if not oppgitt.isdigit():
                return await self._avvis(send, "body_lengde_ugyldig", rid)
            if int(oppgitt) > self.maks:
                # Åpenbart for stor: avvis uten å lese en eneste byte.
                return await self._avvis(send, "body_for_stor", rid)

        kropp = bytearray()
        while True:
            melding = await receive()
            if melding["type"] == "http.disconnect":
                return
            kropp += melding.get("body", b"")
            if len(kropp) > self.maks:
                return await self._avvis(send, "body_for_stor", rid)
            if not melding.get("more_body", False):
                break

        ferdig = False

        async def replay():
            nonlocal ferdig
            if ferdig:
                return {"type": "http.disconnect"}
            ferdig = True
            return {"type": "http.request", "body": bytes(kropp),
                    "more_body": False}

        scope["state"]["kropp"] = bytes(kropp)
        return await self.app(scope, replay, send)

    async def _avvis(self, send, kode: str, rid: str):
        if self.logg is not None:
            self.logg.hendelse(kode, rid)
        rad = feiltabell.FEIL[kode]
        data = json.dumps({"feil": kode, "request_id": rid}).encode("utf-8")
        await send({"type": "http.response.start", "status": rad.http,
                    "headers": [(b"content-type", b"application/json"),
                                (b"x-request-id", rid.encode("ascii"))]})
        await send({"type": "http.response.body", "body": data})


def _nytt_request_id() -> str:
    """Alltid serveren, aldri klienten (korreksjon 3 pkt. 1).

    En klientstyrt request_id kan kollidere med en annen tenants, og den
    havner rett i revisjonsloggen og i unntakshistorikken som sporingsnøkkel.
    """
    return secrets.token_hex(12)


# ---------------------------------------------------------------------------
# Applikasjonen
# ---------------------------------------------------------------------------

class Tjeneste:
    """Immutable app-state (korreksjon 1 siste kulepunkt).

    Nøkkelregisteret lastes og valideres ÉN gang her, ved boot. Ingen
    fil- eller miljølesing per forespørsel: en request som leser nøkler fra
    disk er en request som kan endre sikkerhetsatferd midt i drift, og et
    register som byttes under føttene på en pågående beslutning er ikke
    etterprøvbart.
    """

    def __init__(self, dsn: str, *, bind_vert: str = "127.0.0.1",
                 poolstorrelse: int = 20, logg: Sikkerhetslogg | None = None,
                 rate_per_min: int | None = None) -> None:
        krev_lovlig_bind(bind_vert)
        self.pepper = krev_pepper()
        self.cursorpepper = cursorpepper(self.pepper)
        kryptering.krev_kek()
        self.nokler = last_nokler()            # kaster => prosessen starter ikke
        if not self.nokler:
            raise BootNekt("nøkkelregisteret er tomt")
        self.bind_vert = bind_vert
        self.logg = logg or Sikkerhetslogg()
        self.rate = Rategrense(rate_per_min if rate_per_min is not None
                               else int(os.environ.get("DISPONIT_RATE_PER_MIN",
                                                       STANDARD_RATE_PER_MIN)))
        self.pool = Tilkoblingspool(dsn, poolstorrelse)
        conn = self.pool.hent()
        try:
            self.migrasjoner = krev_migrasjonstilstand(conn)
        finally:
            self.pool.gi_tilbake(conn)


class Autentisert:
    __slots__ = ("tenant", "rolle", "scopes", "token_id")

    def __init__(self, tenant, rolle, scopes, token_id):
        self.tenant, self.rolle = tenant, rolle
        self.scopes, self.token_id = set(scopes or ()), token_id

    @property
    def aktor(self) -> str:
        """Aktøren som havner i revisjonsloggen og unntakshistorikken.

        Token-ID-en, ikke hemmeligheten, og aldri noe fra payloaden.
        """
        return f"token:{self.token_id}"

    def kontekst(self) -> EvaluationContext:
        return EvaluationContext(tenant_id=self.tenant, aktor_rolle=self.rolle,
                                 autentisert=True, kilde="api_token")


def _mac(pepper: str, secret: str) -> str:
    return hmac.new(pepper.encode("utf-8"), secret.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def preauth(tjeneste: Tjeneste, conn: psycopg.Connection,
            authorization: str | None) -> Autentisert | None:
    """PRE-AUTH-TRANSAKSJONEN (korreksjon 3).

    Den kjenner ingen tenant og rører ingen tenantbundne tabeller — kun
    `verifiser_token`, som er SECURITY DEFINER og eid av en annen rolle.
    Det er hele poenget med å skille den ut: før tokenet er verifisert
    FINNES det ingen tenant å sette i `disponit.tenant`, og «SET LOCAL som
    første statement» var derfor sirkulær som opprinnelig formulert.

    Transaksjonen lukkes her. Forretningstransaksjonen starter etterpå, med
    SET LOCAL fra resultatet.
    """
    try:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        raa = authorization[7:].strip()
        token_id, _, secret = raa.partition(".")
        if not token_id or not secret:
            return None
        rad = conn.execute("SELECT tenant, rolle, scopes FROM"
                           " verifiser_token(%s, %s)",
                           (token_id, _mac(tjeneste.pepper, secret))).fetchone()
        if rad is None:
            return None
        return Autentisert(rad[0], rad[1], rad[2], token_id)
    finally:
        conn.commit()      # pre-auth eier og lukker sin egen transaksjon


def kanonisk_json(kropp: dict, status: int = 200,
                  headers: dict | None = None) -> Response:
    """Alle svar serialiseres med SORTERTE nøkler.

    Ikke en stilsak. Idempotenskontrakten (v3-delta pkt. 6) krever at 20
    samtidige like forespørsler gir byte-identiske svar, og svaret lagres
    som JSONB. PostgreSQL bevarer ikke nøkkelrekkefølgen i JSONB — den
    normaliseres etter lengde og deretter bytevis — så et lagret svar kom
    tilbake med en ANNEN nøkkelrekkefølge enn det ferske. Innholdet var
    identisk, bytene var det ikke.

    Med sortering på vei ut blir byte-identitet en egenskap ved INNHOLDET,
    som er det kravet faktisk handler om. Fanget av testen; uten den ville
    forskjellen først dukket opp hos en klient som sammenligner svar.
    """
    data = json.dumps(kropp, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    return Response(content=data, status_code=status,
                    media_type="application/json", headers=headers or {})


def _feilsvar(kode: str, rid: str, http: int | None = None) -> Response:
    rad = feiltabell.FEIL[kode]
    return kanonisk_json({"feil": kode, "request_id": rid},
                         http or rad.http, {"x-request-id": rid})


def lag_app(dsn: str | None = None, **kwargs) -> Starlette:
    """ENESTE vei til en app. Boot-sjekkene kjører her og kaster BootNekt.

    Det er derfor det ikke finnes en modulnivå-`app` å importere: en app
    som kan konstrueres uten sjekkene, blir før eller siden konstruert uten
    dem.
    """
    dsn = dsn or os.environ.get("DATABASE_URL") or ""
    if not dsn:
        raise BootNekt("DATABASE_URL mangler")
    tjeneste = Tjeneste(dsn, **kwargs)

    def beslutning(request: Request) -> Response:
        return _beslutning(tjeneste, request)

    def unntak(request: Request) -> Response:
        return _unntak(tjeneste, request)

    def live(request: Request) -> Response:
        # Ingen DB-avhengighet, ingen detaljer (v2 Del 3.1): «prosessen
        # kjører» er hele svaret. Et /live som spør databasen gjør en
        # DB-hikke om til en restart-storm.
        return kanonisk_json({"status": "ok"})

    def ready(request: Request) -> Response:
        return _ready(tjeneste, request)

    app = Starlette(routes=[
        Route("/v1/beslutning", beslutning, methods=["POST"]),
        Route("/v1/unntak", unntak, methods=["GET"]),
        Route("/live", live, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
    ])
    app.state.tjeneste = tjeneste
    ytre = KroppsgrenseMiddleware(app, logg=tjeneste.logg)
    ytre.tjeneste = tjeneste          # testene og lasttesten trenger den
    return ytre                        # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Endepunktene
# ---------------------------------------------------------------------------

def _rid(request: Request) -> str:
    rid = request.scope.get("state", {}).get("request_id")
    if not rid:
        rid = _nytt_request_id()
        request.scope.setdefault("state", {})["request_id"] = rid
    return rid


def _autentiser(tjeneste: Tjeneste, request: Request, conn, rid: str,
                paakrevd_scope: str) -> Autentisert:
    auth = preauth(tjeneste, conn, request.headers.get("authorization"))
    if auth is None:
        tjeneste.logg.hendelse("token_ugyldig", rid)
        raise kjerne.Feilsvar("token_ugyldig")
    if not tjeneste.rate.slipp_gjennom(auth.token_id):
        tjeneste.logg.hendelse("rate_grense", rid, auth.tenant)
        raise kjerne.Feilsvar("rate_grense")
    if paakrevd_scope not in auth.scopes:
        tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                               scope=paakrevd_scope)
        raise kjerne.Feilsvar("scope_mangler")
    return auth


def _beslutning(tjeneste: Tjeneste, request: Request) -> Response:
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            auth = _autentiser(tjeneste, request, conn, rid, "decision:write")
        except kjerne.Feilsvar as f:
            return _feilsvar(f.kode, rid)

        nokkel = request.headers.get("idempotency-key")
        if not nokkel or not nokkel.strip():
            return _feilsvar("idempotensnokkel_mangler", rid)

        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            data = json.loads(raa.decode("utf-8"))
        except Exception:
            tjeneste.logg.hendelse("request_feilformet", rid, auth.tenant)
            return _feilsvar("request_feilformet", rid)
        if not isinstance(data, dict) or not isinstance(data.get("event"), dict) \
                or not isinstance(data.get("policy_id"), str):
            tjeneste.logg.hendelse("request_feilformet", rid, auth.tenant)
            return _feilsvar("request_feilformet", rid)

        try:
            svar = kjerne.behandle(
                conn, auth.kontekst(), policy_id=data["policy_id"],
                event=data["event"], idempotency_key=nokkel.strip(),
                request_id=rid, aktor=auth.aktor, nokler=tjeneste.nokler)
        except kjerne.Feilsvar as f:
            art = "drift" if "drift" in f.rad.routing else "sikkerhet"
            tjeneste.logg.hendelse(f.kode, rid, auth.tenant, art=art)
            return _feilsvar(f.kode, rid)
        except Exception as e:
            # Nødlogg per v2 Del 4.1: uten payload, og svaret merkes IKKE
            # som auditert. En beslutning uten sikret revisjonslogg er ingen
            # gjennomført beslutning.
            #
            # `Exception` og ikke bare `psycopg.Error` med vilje: enhver
            # uventet feil etterlater oss uten bevis for at loggposten ble
            # committet, og da er eneste ærlige svar det samme. Å slippe
            # andre exceptions videre ville gitt klienten en stack trace og
            # oss en beslutning uten kjent utfall.
            tjeneste.logg.hendelse("logging_feilet", rid, auth.tenant,
                                   art="drift", feiltype=type(e).__name__)
            return _feilsvar("logging_feilet", rid)

        if svar.sikkerhetshendelse:
            tjeneste.logg.hendelse(svar.sikkerhetshendelse, rid, auth.tenant,
                                   unntak_id=svar.unntak_id)
        if svar.driftshendelse:
            tjeneste.logg.hendelse(svar.driftshendelse, rid, auth.tenant,
                                   art="drift", unntak_id=svar.unntak_id)
        kropp = dict(svar.kropp)
        kropp.pop("http", None)      # internt felt, aldri ut til klienten
        return kanonisk_json(kropp, svar.http,
                             {"x-request-id": rid,
                              "idempotent-replay": "1" if svar.replay else "0"})
    finally:
        tjeneste.pool.gi_tilbake(conn)


def _unntak(tjeneste: Tjeneste, request: Request) -> Response:
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            auth = _autentiser(tjeneste, request, conn, rid, "exceptions:read")
        except kjerne.Feilsvar as f:
            return _feilsvar(f.kode, rid)

        try:
            # --- Sesjonskontekst FØRST i den autentiserte transaksjonen ----
            # NØYAKTIG samme inngang som beslutningsveien bruker i
            # `kjerne._flyt` steg 1, og av samme grunn: pre-auth er ferdig og
            # committet, tenanten er verifisert, og aktør og request-id kommer
            # fra tokenet og fra serveren — aldri fra klienten.
            #
            # Her sto det tidligere en `sett_tenant()` rett før SELECT-en.
            # Den satte én av tre sesjonsvariabler, og plasseringen gjorde at
            # enhver databaseoperasjon som senere ble lagt til OVENFOR ville
            # kjørt helt uten kontekst. En delvis kontekstsetting er ikke en
            # svakere utgave av porten — den er en ANNEN port enn den
            # beslutningsveien har, og to utgaver av samme kontroll er
            # duplikatformen som ga P1 nr. 4 i PR-002.
            #
            # Kravet «første databaseoperasjon etter preauth» er derfor
            # plassert her og ikke rett før lesingen: alt under er ren
            # parametervalidering i minnet, og den dagen noen legger inn et
            # oppslag der, ligger konteksten allerede foran det.
            sett_kontekst(conn, auth.tenant, auth.aktor, rid)

            sakstype = request.query_params.get("sakstype", "normal")
            if sakstype not in ("normal", "sikkerhet", "drift"):
                return _feilsvar("request_feilformet", rid)
            if sakstype != "normal" and "security:read" not in auth.scopes:
                # v3-delta pkt. 5: sikkerhets- og driftskøene er egne køer med
                # eget scope. `exceptions:read` alene ser dem aldri.
                tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                       scope="security:read")
                return _feilsvar("scope_mangler", rid)

            status = request.query_params.get("status")
            if status is not None and status not in ("ny", "under_behandling",
                                                     "løst", "avvist"):
                return _feilsvar("request_feilformet", rid)
            try:
                grense = min(
                    int(request.query_params.get("limit", SIDE_STANDARD)),
                    SIDE_MAKS)
                if grense < 1:
                    raise ValueError
            except ValueError:
                return _feilsvar("request_feilformet", rid)

            etter = None
            raa_cursor = request.query_params.get("cursor")
            if raa_cursor:
                try:
                    etter = cursormodul.les(raa_cursor, auth.tenant,
                                            tjeneste.cursorpepper)
                except cursormodul.CursorUgyldig:
                    tjeneste.logg.hendelse("cursor_ugyldig", rid, auth.tenant)
                    return _feilsvar("cursor_ugyldig", rid)

            sql = ("SELECT id, ts, handling, kategori, prioritet, status,"
                   " sakstype FROM unntak"
                   " WHERE tenant=%s AND sakstype=%s")
            args: list = [auth.tenant, sakstype]
            if status is not None:
                sql += " AND status=%s"
                args.append(status)
            if etter is not None:
                sql += " AND (ts, id) < (%s, %s)"
                args += [etter[0], etter[1]]
            sql += " ORDER BY ts DESC, id DESC LIMIT %s"
            args.append(grense)
            rader = conn.execute(sql, tuple(args)).fetchall()
            conn.rollback()
        except psycopg.Error as e:
            tjeneste.logg.hendelse("db_utilgjengelig", rid, auth.tenant,
                                   art="drift", feiltype=type(e).__name__)
            return _feilsvar("db_utilgjengelig", rid)

        saker = [{"id": r[0], "ts": r[1].isoformat(), "handling": r[2],
                  "kategori": r[3], "prioritet": r[4], "status": r[5],
                  "sakstype": r[6]} for r in rader]
        # Payload er IKKE med — og kan ikke bli det ved et uhell, fordi
        # kolonnen aldri hentes. `exceptions:manage` (PR-006) er veien dit.
        neste = (cursormodul.lag(auth.tenant, rader[-1][1], rader[-1][0],
                                 tjeneste.cursorpepper)
                 if len(rader) == grense else None)
        return kanonisk_json({"saker": saker, "neste_cursor": neste,
                              "request_id": rid}, 200, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)


def _ready(tjeneste: Tjeneste, request: Request) -> Response:
    """Kun loopback, og kun ikke-tenantbundet status (korreksjon 3).

    Later aldri som den har tokenkontekst: den setter ingen
    `disponit.tenant`, leser ingen tenanttabeller og svarer ok/ikke-ok uten
    versjonsdetaljer. Et readiness-endepunkt som lekker hvilken
    migrasjonsversjon som kjører, er et rekognoseringsverktøy.
    """
    klient = request.client.host if request.client else ""
    if not er_loopback(klient):
        # 404, ikke 403: at endepunktet finnes er i seg selv informasjon.
        return kanonisk_json({"feil": "ukjent"}, 404)
    try:
        conn = tjeneste.pool.hent(timeout=2.0)
    except (TimeoutError, psycopg.Error):
        return kanonisk_json({"status": "ikke_klar"}, 503)
    try:
        conn.execute("SELECT 1").fetchone()
        faktisk = [r[0] for r in conn.execute(
            "SELECT versjon FROM migrasjoner ORDER BY versjon").fetchall()]
        conn.rollback()
        klar = (faktisk == tjeneste.migrasjoner
                and bool(tjeneste.nokler))
        return kanonisk_json({"status": "ok" if klar else "ikke_klar"},
                             200 if klar else 503)
    except psycopg.Error:
        return kanonisk_json({"status": "ikke_klar"}, 503)
    finally:
        tjeneste.pool.gi_tilbake(conn)
