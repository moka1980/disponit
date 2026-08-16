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
import uuid
from datetime import datetime, timezone
from pathlib import Path

import oppdragskontrakt
import psycopg
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from db import kryptering
from db.pg import koble, sett_kontekst
from policy_validator.attestering import last_nokler
from .mac_register import last_mac_register
from policy_validator.engine import EvaluationContext

from . import cursor as cursormodul
from . import feil as feiltabell
from . import kjerne

MAKS_KROPP = 256 * 1024          # v2 Del 3.4
# PR-014b §7: en artefakt-klartekst kan være opptil 1 MiB JCS-kanonisert (DB-CHECK
# på serverberegnet størrelse). Codex: gyldig JSON kan representere ett tegn som
# et 6-byte `\uXXXX`-escape, så et dokument godt under 1 MiB kanonisk kan ha en
# vesentlig større WIRE-form. Transport-grensen tillater derfor verste-falls
# JSON-ekspansjon (~6×) + struktur/jti-overhead; JCS-størrelsen (1 MiB) er den
# egentlige porten og sjekkes i handleren FØR lagring.
MAKS_ARTEFAKT_KROPP = 6 * 1024 * 1024 + 64 * 1024
STORE_KROPP_RUTER = frozenset({"/v1/artefakt"})

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
        # GET/HEAD/DELETE bærer ingen kropp i dette API-et (DELETE /v1/sesjon
        # er ren logout). Kroppsgrensen gjelder de metodene som FAKTISK tar
        # imot data — å kreve Content-Length på en bodyless DELETE ville
        # avvist en helt vanlig `fetch(url,{method:'DELETE'})`.
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD",
                                                           "DELETE"):
            return await self.app(scope, receive, send)

        headere = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        rid = scope.setdefault("state", {}).get("request_id") or _nytt_request_id()
        scope["state"]["request_id"] = rid

        # PR-014b §7: opplastingsruten tåler en større kropp (1 MiB-artefakt).
        maks = (MAKS_ARTEFAKT_KROPP if scope.get("path") in STORE_KROPP_RUTER
                else self.maks)
        oppgitt = headere.get("content-length")
        chunked = "chunked" in headere.get("transfer-encoding", "").lower()
        if oppgitt is None and not chunked:
            return await self._avvis(send, "body_lengde_ugyldig", rid)
        if oppgitt is not None:
            if not oppgitt.isdigit():
                return await self._avvis(send, "body_lengde_ugyldig", rid)
            if int(oppgitt) > maks:
                # Åpenbart for stor: avvis uten å lese en eneste byte.
                return await self._avvis(send, "body_for_stor", rid)

        kropp = bytearray()
        while True:
            melding = await receive()
            if melding["type"] == "http.disconnect":
                return
            kropp += melding.get("body", b"")
            if len(kropp) > maks:
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
        # MAC-register for menneskelige godkjenningskonvolutter (PR-012). Samme
        # oppstartsperre som attestasjonsnøklene: mangler/ugyldig register →
        # prosessen nekter start, så porten aldri kjører uten en signeringsnøkkel.
        self.mac_register = last_mac_register()
        # PR-013 (V8/port 13): semantikk- og miljøverifikasjon ved oppstart. En
        # CI-port beskytter ikke produksjon om verten kjører annen tzdata enn
        # releasen ble bygget med — da tolker motoren tidsvinduer annerledes enn
        # klassifikatoren beviste. Fail-closed: avvik → prosessen nekter start.
        from policy_validator import semantikk
        semantikk.verifiser_oppstartsmiljo()
        self.bind_vert = bind_vert
        self.logg = logg or Sikkerhetslogg()
        self.rate = Rategrense(rate_per_min if rate_per_min is not None
                               else int(os.environ.get("DISPONIT_RATE_PER_MIN",
                                                       STANDARD_RATE_PER_MIN)))
        # Rollback-kontrakten (rollback-m01-v1). Deaktivering av en modul
        # skal gi et DEFINERT svar, ikke en tilfeldig 500 eller en hengende
        # forespørsel — det er forskjellen på en rollback og et utfall.
        #
        # Lest ved BOOT, ikke per forespørsel. En fillesing i request-path
        # ville kostet av en ytelsesmargin som allerede er tynn (p99 207 ms),
        # og deaktivering er uansett en operasjon som restarter prosessen.
        # `deaktivering_effektiv_s` i artefaktet måler nettopp den runden.
        self.inaktive_moduler = frozenset(
            m.strip() for m in
            os.environ.get("DISPONIT_INAKTIVE_MODULER", "").split(",")
            if m.strip())
        self.pool = Tilkoblingspool(dsn, poolstorrelse)
        conn = self.pool.hent()
        try:
            self.migrasjoner = krev_migrasjonstilstand(conn)
        finally:
            self.pool.gi_tilbake(conn)


class Kapabilitet:
    """En reservert arbeidskapabilitet (PR-006 v3-delta pkt. 1, v4 pkt. 1).

    Bæres gjennom forespørselen slik at `kjerne.behandle()` kan REVALIDERE
    mot unntaksraden og brenne den i samme commit som beslutningen. Feltene
    her er utstedelsesverdiene — de er IKKE fasit alene, og det er poenget
    med revalideringen.
    """
    __slots__ = ("jti", "tenant", "unntak_id", "tillatt_handling",
                 "repair_operation_id", "claim_id", "claim_generation")

    def __init__(self, jti, tenant, unntak_id, tillatt_handling,
                 repair_operation_id, claim_id, claim_generation):
        self.jti, self.tenant = jti, tenant
        self.unntak_id, self.tillatt_handling = unntak_id, tillatt_handling
        self.repair_operation_id = repair_operation_id
        self.claim_id, self.claim_generation = claim_id, claim_generation


class Autentisert:
    __slots__ = ("tenant", "rolle", "scopes", "token_id", "kapabilitet")

    def __init__(self, tenant, rolle, scopes, token_id, kapabilitet=None):
        self.tenant, self.rolle = tenant, rolle
        self.scopes, self.token_id = set(scopes or ()), token_id
        self.kapabilitet = kapabilitet

    @property
    def aktor(self) -> str:
        """Aktøren som havner i revisjonsloggen og unntakshistorikken.

        Token-ID-en, ikke hemmeligheten, og aldri noe fra payloaden. For en
        arbeidskapabilitet er det jti-en: da kan man i ettertid se nøyaktig
        hvilken engangsfullmakt som bar beslutningen, ikke bare at «M-37
        gjorde det».
        """
        return f"token:{self.token_id}"

    def kontekst(self) -> EvaluationContext:
        return EvaluationContext(
            tenant_id=self.tenant, aktor_rolle=self.rolle, autentisert=True,
            kilde="arbeidskapabilitet" if self.kapabilitet else "api_token")


def _mac(pepper: str, secret: str) -> str:
    return hmac.new(pepper.encode("utf-8"), secret.encode("utf-8"),
                    hashlib.sha256).hexdigest()


#: Scopene en arbeidskapabilitet gir. NØYAKTIG ett, og ikke konfigurerbart.
#:
#: M-37 skal kunne be om én beslutning og ingenting annet. Ga kapabiliteten
#: `exceptions:read`, kunne en kompromittert arbeider lest hele køen for
#: tenanten; ga den `exceptions:manage`, kunne den lukket saker uten
#: evidens. Mengden står som en frozenset i koden og ikke som en kolonne,
#: fordi en kolonne kan endres av den som kan skrive til tabellen.
KAPABILITETSSCOPES = frozenset({"decision:write"})


def _preauth_kapabilitet(conn: psycopg.Connection, jti: str,
                         request_id: str) -> Autentisert | None:
    """Innløser en arbeidskapabilitet: `utstedt -> reservert`, atomisk.

    Reservasjonen — ikke forbruket. Kapabiliteten brennes først i SAMME
    commit som den auditerte beslutningen (`kjerne._avslutt`). Skillet er
    v4-delta pkt. 1 punkt 3 og 5, og grunnen er konkret: brennes den her,
    og transaksjonen ruller etterpå, er engangsfullmakten borte uten at det
    finnes en loggpost som viser hva den ble brukt til. Da kan verken
    arbeideren gjenoppta eller revisor rekonstruere.

    Gjenopptak er bundet til request_id: samme forespørsel kan ta opp igjen
    en reservasjon den selv eier, enhver annen avvises.
    """
    if not jti:
        return None
    rad = conn.execute(
        "SELECT tenant, unntak_id, tillatt_handling, repair_operation_id,"
        " claim_id, claim_generation, aktor_rolle"
        "  FROM reserver_kapabilitet(%s, %s)",
        (jti, request_id)).fetchone()
    if rad is None:
        return None
    kap = Kapabilitet(jti, rad[0], rad[1], rad[2], rad[3], rad[4], rad[5])
    # Rollen er sakens OPPRINNELIGE aktørrolle, frosset ved utstedelsen fra
    # den reviderte loggposten — ikke en rolle M-37 har. Sto det `"m37"` her,
    # ville reparasjonen krevd at kunden ga M-37 en egen fullmakt i policyen,
    # og «null egne fullmakter» hadde vært en påstand uten mekanisme.
    #
    # MÅLT: med `"m37"` svarte motoren `rolle_ikke_tillatt` på hver eneste
    # fase-2-beslutning, og saken gikk til manuell. Porten fantes; det var
    # identiteten som ikke gjorde det.
    #
    # `token_id` blir fortsatt jti-en, så unntakshistorikken peker på nøyaktig
    # denne engangsfullmakten, og `kilde='arbeidskapabilitet'` skiller
    # reparasjonen fra den opprinnelige beslutningen i revisjonsloggen.
    if not rad[6]:
        return None
    return Autentisert(kap.tenant, rad[6], KAPABILITETSSCOPES, jti, kap)


def preauth(tjeneste: Tjeneste, conn: psycopg.Connection,
            authorization: str | None, request_id: str = "") -> Autentisert | None:
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
        if authorization and authorization.startswith("Kapabilitet "):
            return _preauth_kapabilitet(conn, authorization[12:].strip(),
                                        request_id)
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
    # PR-009: systemd-credentials hydreres FØR noen env-lesing. Utenfor
    # systemd er dette en no-op, og en allerede satt variabel vinner alltid.
    from db.hemmeligheter import last_credentials
    last_credentials()
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

    def oppdrag_claim(request: Request) -> Response:
        return _oppdrag_claim(tjeneste, request)

    def oppdrag_kvittering(request: Request) -> Response:
        return _oppdrag_kvittering(tjeneste, request)

    def artefakt_upload(request: Request) -> Response:
        return _artefakt_upload(tjeneste, request)

    # PR-008: lese-endepunktene. Importen ligger her — ETTER at modulens
    # hjelpere er definert — fordi `lesing` importerer dem på modulnivå.
    from . import lesing

    def oversikt(request: Request) -> Response:
        return lesing.oversikt(tjeneste, request)

    def beslutninger(request: Request) -> Response:
        return lesing.beslutninger(tjeneste, request)

    def beslutning_detalj(request: Request) -> Response:
        return lesing.beslutning_detalj(tjeneste, request)

    def unntak_detalj(request: Request) -> Response:
        return lesing.unntak_detalj(tjeneste, request)

    def unntak_historikk(request: Request) -> Response:
        return lesing.unntak_historikk(tjeneste, request)

    def unntak_handling(request: Request) -> Response:
        from . import unntaksbehandling
        return unntaksbehandling.handling_endepunkt(
            tjeneste, request, request.path_params["id"])

    def unntak_domeneattestasjon(request: Request) -> Response:
        # PR-015 §4: fire øyne. EGEN rute og EGET scope — å henge den på
        # /handling ville gitt `exceptions:approve` cross-tenant
        # domeneautoritet som en ren bieffekt av å kunne behandle unntak.
        from . import domeneovertakelse
        return domeneovertakelse.attester_endepunkt(
            tjeneste, request, request.path_params["id"])

    def policy_aktiv(request: Request) -> Response:
        return lesing.policy_aktiv(tjeneste, request)

    def utrulling(request: Request) -> Response:
        return lesing.utrulling(tjeneste, request)

    # PR-011: M-1 kundeflate — same-origin, DB-fri statisk servering. UI-ets
    # egne handlere tar bare `request` (rører aldri `tjeneste`/poolen), så
    # de refereres direkte i rutelisten.
    from ui import server as uiserver

    # PR-010: OIDC-sesjonsrutene.
    from . import sesjon as sesjonmodul

    def oidc_start(request: Request) -> Response:
        return sesjonmodul.oidc_start(tjeneste, request)

    def oidc_callback(request: Request) -> Response:
        return sesjonmodul.oidc_callback(tjeneste, request)

    def sesjon_hvem(request: Request) -> Response:
        return sesjonmodul.sesjon_hvem(tjeneste, request)

    def sesjon_logout(request: Request) -> Response:
        return sesjonmodul.sesjon_logout(tjeneste, request)

    # PR-013: policyadministrasjon — utkast-CRUD + aktivering (fire-øyne).
    from . import policyadmin_http

    def pa_opprett_utkast(request: Request) -> Response:
        return policyadmin_http.opprett_utkast_endepunkt(tjeneste, request)

    def pa_list_utkast(request: Request) -> Response:
        return policyadmin_http.list_utkast_endepunkt(tjeneste, request)

    def pa_maler(request: Request) -> Response:
        return policyadmin_http.maler_endepunkt(tjeneste, request)

    def pa_hent_utkast(request: Request) -> Response:
        return policyadmin_http.hent_utkast_endepunkt(tjeneste, request)

    def pa_rediger_utkast(request: Request) -> Response:
        return policyadmin_http.rediger_utkast_endepunkt(tjeneste, request)

    def pa_valider_utkast(request: Request) -> Response:
        return policyadmin_http.valider_utkast_endepunkt(tjeneste, request)

    def pa_varsel_liste(request: Request) -> Response:
        return policyadmin_http.varsel_liste_endepunkt(tjeneste, request)

    def pa_varsel_lest(request: Request) -> Response:
        return policyadmin_http.varsel_lest_endepunkt(tjeneste, request)

    def pa_varselvalg(request: Request) -> Response:
        return policyadmin_http.varselvalg_endepunkt(tjeneste, request)

    def pa_forkast_utkast(request: Request) -> Response:
        return policyadmin_http.forkast_utkast_endepunkt(tjeneste, request)

    def pa_apne_runde(request: Request) -> Response:
        return policyadmin_http.apne_runde_endepunkt(tjeneste, request)

    def pa_attester(request: Request) -> Response:
        return policyadmin_http.attester_endepunkt(tjeneste, request)

    app = Starlette(routes=[
        Route("/v1/beslutning", beslutning, methods=["POST"]),
        Route("/v1/unntak", unntak, methods=["GET"]),
        # PR-006: outbox-protokollen. Den syntetiske eiermodulen på staging
        # bruker NØYAKTIG disse to endepunktene og skriver aldri i
        # databasen direkte — det er en av de åtte evidensbevisene, og en
        # statisk sjekk i testsuiten håndhever den.
        Route("/v1/oppdrag/claim", oppdrag_claim, methods=["POST"]),
        Route("/v1/oppdrag/kvittering", oppdrag_kvittering, methods=["POST"]),
        Route("/v1/artefakt", artefakt_upload, methods=["POST"]),
        # PR-008: rent lesende kundeflate. Merk rekkefølgen: den statiske
        # `/v1/policy/aktiv` registreres FØR mønsterruter kunne ha slukt
        # den, og detaljrutene bruker {id:int} så en ikke-numerisk sti er
        # 404 fra routeren, ikke en kodevei.
        Route("/v1/oversikt", oversikt, methods=["GET"]),
        Route("/v1/beslutninger", beslutninger, methods=["GET"]),
        Route("/v1/beslutninger/{id:int}", beslutning_detalj, methods=["GET"]),
        Route("/v1/unntak/{id:int}", unntak_detalj, methods=["GET"]),
        Route("/v1/unntak/{id:int}/historikk", unntak_historikk,
              methods=["GET"]),
        Route("/v1/unntak/{id:int}/handling", unntak_handling,
              methods=["POST"]),
        Route("/v1/unntak/{id:int}/domeneattestasjon",
              unntak_domeneattestasjon, methods=["POST"]),
        Route("/v1/policy/aktiv", policy_aktiv, methods=["GET"]),
        # Utrullingsplanen: øktbundet, fordi den ellers måtte ligge i den
        # statisk serverte klientbunten der hvem som helst kunne lese hver
        # tenants plan og modultildeling.
        Route("/v1/utrulling", utrulling, methods=["GET"]),
        # PR-013: policyadministrasjon. Kolleksjonsrutene FØR mønsterrutene, og
        # de spesifikke handlings-subrutene (.../valider osv.) er egne stier så
        # {utkast_id:str} aldri slukter dem.
        Route("/v1/policymaler", pa_maler, methods=["GET"]),
        Route("/v1/policyutkast", pa_opprett_utkast, methods=["POST"]),
        Route("/v1/policyutkast", pa_list_utkast, methods=["GET"]),
        Route("/v1/policyutkast/{utkast_id:str}/valider", pa_valider_utkast,
              methods=["POST"]),
        Route("/v1/varsel", pa_varsel_liste, methods=["GET"]),
        Route("/v1/varsel/{varsel_id:str}/lest", pa_varsel_lest,
              methods=["POST"]),
        Route("/v1/varselvalg", pa_varselvalg, methods=["POST"]),
        Route("/v1/policyutkast/{utkast_id:str}/forkast", pa_forkast_utkast,
              methods=["POST"]),
        Route("/v1/policyutkast/{utkast_id:str}/aktiveringsrunde",
              pa_apne_runde, methods=["POST"]),
        Route("/v1/policyutkast/{utkast_id:str}/attester", pa_attester,
              methods=["POST"]),
        Route("/v1/policyutkast/{utkast_id:str}", pa_hent_utkast,
              methods=["GET"]),
        Route("/v1/policyutkast/{utkast_id:str}", pa_rediger_utkast,
              methods=["PUT"]),
        # PR-010: OIDC-sesjon. /start er POST (v5 §1), callback er GET
        # (navigasjon fra IdP), /v1/sesjon er GET (hvem) + DELETE (logout).
        Route("/v1/oidc/start", oidc_start, methods=["POST"]),
        Route("/v1/oidc/callback", oidc_callback, methods=["GET"]),
        Route("/v1/sesjon", sesjon_hvem, methods=["GET"]),
        Route("/v1/sesjon", sesjon_logout, methods=["DELETE"]),
        Route("/live", live, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
        # PR-011: M-1 kundeflate. Skallet på "/", ressurser under /ui/.
        # Locale-ruten registreres FØR den generelle asset-ruten, ellers
        # ville {sti:path} slukt "locale/nb".
        Route("/", uiserver.ui_index, methods=["GET"]),
        Route("/ui/locale/{sprak}", uiserver.ui_locale, methods=["GET"]),
        # /ui/oppsett.json er DYNAMISK (deploy-satt provider), registreres FØR
        # den generelle asset-ruten som ellers ville slukt den.
        Route("/ui/oppsett.json", uiserver.ui_oppsett, methods=["GET"]),
        Route("/ui/{sti:path}", uiserver.ui_asset, methods=["GET"]),
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


#: PR-008: de fire lese-scopene brukersesjonen kan holde.
LESESCOPES = frozenset({"decisions:read", "exceptions:read", "policy:read",
                        "security:read"})

#: Roller som er ALLOWLISTET til kun lesing. `bruker`-rollen når aldri et
#: muterende endepunkt — selv om noen skulle utstede et bruker-token med
#: `decision:write` i scopes-kolonnen, stopper rollen her (default-deny i
#: KODEN, ikke bare i utstedelsen — v2 pkt. 9).
LESEROLLER = frozenset({"bruker"})

#: PR-012: de ENESTE muterende scopene en browsersesjon får nå — den
#: menneskelige unntaksbehandlingen. CSRF håndheves i selve endepunktet
#: (dobbel-innsending); carve-outen her slipper dem bare forbi den generelle
#: «browsersesjon når aldri et muterende scope»-porten.
BROWSER_MUTASJONSSCOPES = frozenset({"exceptions:approve", "exceptions:reject",
                                     "exceptions:escalate",
                                     # PR-013: policyadministrasjon. `write`
                                     # (redigere utkast) og `activate` (attestere
                                     # aktivering) er BEVISST ADSKILTE — den som
                                     # kan skrive et utkast skal ikke dermed
                                     # kunne sette det i produksjon (v5 §3, V6).
                                     "policy:write", "policy:activate",
                                     # PR-015 §3/§4: domeneadjudikasjon er en
                                     # MENNESKELIG avgjørelse i PR-012-flaten,
                                     # altså en browsersesjon. Scopet står her
                                     # for å slippe forbi den generelle porten
                                     # — ikke for å gi det til noen: det deles
                                     # aldri ut sammen med exceptions:handle.
                                     "domains:adjudicate"})


def _autentiser(tjeneste: Tjeneste, request: Request, conn, rid: str,
                paakrevd_scope: str) -> Autentisert:
    # PR-010: ÉN autorisasjonsvei for BÅDE browsersesjon (cookie) og
    # maskin-token (Bearer). Begge samtidig → 400 (v2 §8), ingen fallback.
    from . import sesjon as sesjonmodul
    har_cookie = bool(request.cookies.get(sesjonmodul.C_SESJON))
    har_bearer = bool(request.headers.get("authorization"))
    if har_cookie and har_bearer:
        tjeneste.logg.hendelse("dobbel_principal", rid)
        raise kjerne.Feilsvar("dobbel_principal")

    if har_cookie:
        prin = sesjonmodul.slaa_opp_prinsipal(tjeneste, conn, request, rid)
        if prin is None:
            tjeneste.logg.hendelse("sesjon_ugyldig", rid)
            raise kjerne.Feilsvar("sesjon_ugyldig")
        tenant, bid, scopes, _utloper, _roller, _epost = prin
        auth = Autentisert(tenant, "bruker", scopes, f"sesjon:{bid}")
        if paakrevd_scope not in scopes:
            tjeneste.logg.hendelse("scope_mangler", rid, tenant,
                                   scope=paakrevd_scope, rolle="bruker")
            raise kjerne.Feilsvar("scope_mangler")
        if paakrevd_scope not in LESESCOPES \
                and paakrevd_scope not in BROWSER_MUTASJONSSCOPES:
            # En browsersesjon når ALDRI et muterende scope — UNNTATT den
            # CSRF-håndhevede unntaksbehandlingen (PR-012), som endepunktet
            # selv verner med dobbel-innsending.
            tjeneste.logg.hendelse("scope_mangler", rid, tenant,
                                   scope=paakrevd_scope, rolle="bruker")
            raise kjerne.Feilsvar("scope_mangler")
        return auth

    auth = preauth(tjeneste, conn, request.headers.get("authorization"), rid)
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
    if auth.rolle in LESEROLLER and paakrevd_scope not in LESESCOPES:
        tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                               scope=paakrevd_scope, rolle=auth.rolle)
        raise kjerne.Feilsvar("scope_mangler")
    return auth


#: Modulen beslutningsveien tilhører. Deaktiveres den, er hele
#: `/v1/beslutning` ute — og svaret skal si det, ikke feile tilfeldig.
BESLUTNINGSMODUL = "m01_policy"


def _beslutning(tjeneste: Tjeneste, request: Request) -> Response:
    rid = _rid(request)
    if BESLUTNINGSMODUL in tjeneste.inaktive_moduler:
        # FØR tilkoblingen hentes: en deaktivert modul skal ikke bruke en
        # poolplass, og den skal ikke rekke å åpne en transaksjon som må
        # rulles. `halvferdige_transaksjoner = 0` i rollback-artefaktet er
        # en egenskap ved at avvisningen skjer her, ikke lenger nede.
        tjeneste.logg.hendelse("modul_inaktiv", rid, art="drift",
                               modul=BESLUTNINGSMODUL)
        return _feilsvar("modul_inaktiv", rid)
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
                request_id=rid, aktor=auth.aktor, nokler=tjeneste.nokler,
                kapabilitet=auth.kapabilitet)
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


# ---------------------------------------------------------------------------
# PR-006: oppdrags-endepunktene (v3-delta pkt. 2, v4-delta pkt. 3-4)
# ---------------------------------------------------------------------------

ORDRESCOPE = "orders:execute:"

#: PR-008 (v2 pkt. 9): ruteregisteret. HVER rute i appen skal stå her med
#: sitt påkrevde scope — `None` er KUN lovlig for de uautentiserte
#: helsesjekkene. Testsuiten binder registeret mot `lag_app()`s faktiske
#: ruteliste begge veier: en rute uten deklarasjon er en testfeil
#: (fail-closed), og en deklarasjon uten rute er en død regel.
RUTESCOPE: dict[tuple[str, str], str | None] = {
    ("POST", "/v1/beslutning"):              "decision:write",
    ("GET",  "/v1/unntak"):                  "exceptions:read",
    ("POST", "/v1/oppdrag/claim"):           ORDRESCOPE + "<prefiks>",
    ("POST", "/v1/oppdrag/kvittering"):      ORDRESCOPE + "<prefiks>",
    ("POST", "/v1/artefakt"):                "artifacts:upload",
    ("GET",  "/v1/oversikt"):                "decisions:read",
    ("GET",  "/v1/beslutninger"):            "decisions:read",
    ("GET",  "/v1/beslutninger/{id:int}"):   "decisions:read",
    ("GET",  "/v1/unntak/{id:int}"):         "exceptions:read",
    ("GET",  "/v1/unntak/{id:int}/historikk"): "exceptions:read",
    ("POST", "/v1/unntak/{id:int}/handling"): "exceptions:approve",
    # PR-015 §3: cross-tenant domeneautoritet er sitt EGET scope.
    ("POST", "/v1/unntak/{id:int}/domeneattestasjon"): "domains:adjudicate",
    ("GET",  "/v1/policy/aktiv"):            "policy:read",
    # Utrullingsplanen: kundens egen flate, derfor `decisions:read` (som ALLE
    # kunderollene har). Kontrollplanet på tvers krever i tillegg
    # `platform:admin`, og det avgjøres inne i endepunktet — det er en
    # utvidelse av svaret, ikke en annen inngang.
    ("GET",  "/v1/utrulling"):               "decisions:read",
    # PR-013: policyadministrasjon. write/activate er ADSKILTE (V6); lesing er
    # policy:read. Verifiseres per-endepunkt av _autentiser + CSRF.
    ("GET",  "/v1/policymaler"):             "policy:read",
    ("POST", "/v1/policyutkast"):            "policy:write",
    ("GET",  "/v1/policyutkast"):            "policy:read",
    ("POST", "/v1/policyutkast/{utkast_id:str}/valider"): "policy:write",
    ("GET",  "/v1/varsel"):                  "policy:read",
    ("POST", "/v1/varsel/{varsel_id:str}/lest"): "policy:write",
    ("POST", "/v1/varselvalg"):              "policy:write",
    ("POST", "/v1/policyutkast/{utkast_id:str}/forkast"): "policy:write",
    ("POST", "/v1/policyutkast/{utkast_id:str}/aktiveringsrunde"): "policy:activate",
    ("POST", "/v1/policyutkast/{utkast_id:str}/attester"): "policy:activate",
    ("GET",  "/v1/policyutkast/{utkast_id:str}"): "policy:read",
    ("PUT",  "/v1/policyutkast/{utkast_id:str}"): "policy:write",
    # PR-010: OIDC-sesjon. /start og /callback er uautentiserte (de
    # ETABLERER sesjonen); /v1/sesjon GET/DELETE gjelder sesjonen selv og
    # scope-gates ikke — de er sesjonshåndtering, ikke lese-data.
    ("POST", "/v1/oidc/start"):              None,
    ("GET",  "/v1/oidc/callback"):           None,
    ("GET",  "/v1/sesjon"):                  None,
    ("DELETE", "/v1/sesjon"):                None,
    ("GET",  "/live"):                       None,
    ("GET",  "/ready"):                      None,
}


def _modulscope(auth: Autentisert) -> list[str]:
    """Handlingsprefiksene modulen har fullmakt for.

    Scope-formatet er `orders:execute:<handlingsprefiks>`, og LISTEN ER
    LUKKET: er den tom, treffer `claim_neste_oppdrag` ingenting. Det er
    fail-closed og ikke en detalj — en tom prefiksliste tolket som «alle»
    ville gjort et token uten fullmakter til det mektigste i systemet.
    """
    return sorted(s[len(ORDRESCOPE):] for s in auth.scopes
                  if s.startswith(ORDRESCOPE) and len(s) > len(ORDRESCOPE))


def _oppdrag_claim(tjeneste: Tjeneste, request: Request) -> Response:
    """Eiermodulen plukker ett oppdrag. Dekryptering skjer HER, ikke der.

    DEK og KEK forlater aldri API-/kryptolaget (v4-delta pkt. 4).
    Eiermodulen ser verken ciphertext eller nøkkel — den får minimert
    klartekst, filtrert mot oppdragstypens LUKKEDE feltskjema. Prefikset
    alene gir aldri feltbredde: to oppdrag med samme prefiks kan ha helt
    ulike behov, og lot vi navnet styre hva som slipper ut, ville
    feltbredden vært en funksjon av en streng.
    """
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        auth = preauth(tjeneste, conn, request.headers.get("authorization"), rid)
        if auth is None or auth.kapabilitet is not None:
            # En ARBEIDSkapabilitet gir aldri tilgang hit. Den er utstedt
            # for én beslutning på én sak; kunne den plukke oppdrag, ville
            # M-37 kunnet utføre sine egne oppdrag — altså akkurat den
            # sammenblandingen null-fullmaktsprinsippet finnes for.
            tjeneste.logg.hendelse("token_ugyldig", rid)
            return _feilsvar("token_ugyldig", rid)
        if not tjeneste.rate.slipp_gjennom(auth.token_id):
            tjeneste.logg.hendelse("rate_grense", rid, auth.tenant)
            return _feilsvar("rate_grense", rid)
        prefikser = _modulscope(auth)
        if not prefikser:
            tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                   scope=ORDRESCOPE + "<prefiks>")
            return _feilsvar("scope_mangler", rid)

        claim_id = secrets.token_hex(16)
        try:
            # Deployment-identiteten (release/miljø/epoch) for en REGISTRERT
            # oppdragstype må komme fra en AUTENTISERT kilde — modulens token,
            # bundet til dens deployment ved onboarding. Å ta den fra en spoofbar
            # request-parameter ville latt en drenert r1-prosess claime via r2
            # (nettopp det bindingen skal hindre). Til modul-onboarding trår i
            # kraft passeres NULL: en registrert oppdragstype er da IKKE claimbar
            # herfra (fail-closed) — legacy/uregistrert arbeid er upåvirket.
            rad = conn.execute(
                "SELECT id, tenant, unntak_id, oppdragstype, handling,"
                " repair_operation_id, payload_kryptert, key_id, nonce,"
                " owner_generation, utforelsesfrist, evidensfrist"
                "  FROM claim_neste_oppdrag(%s, %s, %s, %s, %s, %s, %s)",
                (auth.rolle, prefikser, claim_id, 300, None, None, None)).fetchone()
            if rad is None:
                conn.rollback()
                return kanonisk_json({"oppdrag": None, "request_id": rid}, 204,
                                     {"x-request-id": rid})

            (opp_id, tenant, unntak_id, oppdragstype, handling, repair_id,
             ct, key_id, nonce, owner_gen, uf, ef) = rad

            # Dekrypteringen krever tenantkontekst — samme port som alle
            # andre veier inn. Konteksten settes ETTER claimen fordi
            # oppdragskøen er på tvers av tenanter (modulen betjener mange),
            # og tenanten er ukjent til claimen har skjedd.
            sett_kontekst(conn, tenant, auth.aktor, rid)
            nokkelrad = conn.execute(
                "SELECT wrapped_dek FROM tenant_nokler"
                " WHERE tenant=%s AND key_id=%s", (tenant, key_id)).fetchone()
            if nokkelrad is None or nokkelrad[0] is None:
                conn.rollback()
                return _feilsvar("tenantnokkel_mangler", rid)
            dek = kryptering._pakk_ut((key_id, nokkelrad[0]), tenant)[1]
            payload = kryptering.dekrypter(dek, ct, nonce, tenant, key_id)

            # MINIMERINGEN SKJER FØR COMMIT (Codex P1, runde 1).
            #
            # Første leveranse committet claimen først og minimerte etterpå.
            # En ukjent oppdragstype eller en feil i minimeringen etterlot da
            # oppdraget permanent `plukket` med en eier som aldri kom
            # tilbake — samme hengende tilstand som en krasjet eiermodul.
            # Her rulles hele claimen i stedet, og oppdraget står fortsatt
            # `opprettet` for neste plukk.
            #
            # `oppdragskontrakt` ligger på core-nivå og ikke i `m37/`
            # NETTOPP fordi begge sider trenger den: arbeideren for å
            # planlegge, API-et for å minimere. Den statiske porten
            # «api/ importerer aldri m37/» skal stoppe ARBEID i
            # forespørselsveien, ikke en delt kontrakt.
            try:
                minimert = oppdragskontrakt.minimer(oppdragstype, payload)
            except oppdragskontrakt.Oppdragstypeukjent:
                conn.rollback()
                tjeneste.logg.hendelse("request_feilformet", rid, tenant,
                                       oppdragstype=oppdragstype)
                return _feilsvar("request_feilformet", rid)

            # Kvitteringskapabiliteten utstedes i SAMME transaksjon som
            # claimen. Feiler utstedelsen, finnes heller ingen claim —
            # alternativet ville vært et plukket oppdrag ingen kan kvittere
            # for, altså den hengende tilstanden i en annen forkledning.
            # Verifikasjonsoppdrag bærer sin generasjon i responsen.
            # Verifikatoren må kunne binde attestasjonen til NØYAKTIG den
            # generasjonen som bestilte den — ellers kunne et bevis fra en
            # gammel runde bli akseptert i en ny.
            verifikasjonsgen = None
            if oppdragstype == "verifikasjon":
                vg = conn.execute(
                    "SELECT generation FROM verifikasjonsgenerasjon"
                    " WHERE tenant=%s AND oppdrag_id=%s",
                    (tenant, opp_id)).fetchone()
                if vg is None:
                    conn.rollback()
                    tjeneste.logg.hendelse("db_utilgjengelig", rid, tenant,
                                           art="drift",
                                           feiltype="generasjon_mangler")
                    return _feilsvar("db_utilgjengelig", rid)
                verifikasjonsgen = vg[0]

            kvittering_jti = secrets.token_hex(16)
            kap = conn.execute(
                "SELECT jti, utloper FROM utsted_kvitteringskapabilitet("
                "%s,%s,%s,%s)",
                (opp_id, claim_id, owner_gen, kvittering_jti)).fetchone()
            if kap is None:
                conn.rollback()
                tjeneste.logg.hendelse("db_utilgjengelig", rid, tenant,
                                       art="drift",
                                       feiltype="kvitteringskapabilitet")
                return _feilsvar("db_utilgjengelig", rid)

            # PR-015 §5: OPPLASTINGSkapabiliteten utstedes her, sammen med
            # kvitteringskapabiliteten, som et SEPARAT token — aldri utledet av
            # den, aldri samme audience. Ikke noe nytt on-demand-endepunkt i v1:
            # et endepunkt som deler ut opplastingsrett på forespørsel ville
            # gjort bindingen til noe modulen ber om, ikke noe serveren vet.
            #
            # Bindingen er SERVERKONTEKSTENS: tenant · oppdrag_id · modul_id ·
            # release_id · kontraktversjon · kontrakt_hash · module_epoch ·
            # artefakttype. Modulen ber ikke om felt; den mottar et token.
            # `oppdrag` stempler modul/kontrakt/epoch ved claim, men IKKE
            # release (017 sier det selv). Releasen hentes derfor fra
            # deploymentregisteret: den ENE `claiming`-deploymenten for samme
            # (modul, kontraktversjon, kontrakt_hash) — det er nettopp den som
            # plukker arbeid, og unikindeksen i 014a garanterer én per miljø.
            # Er den tvetydig eller fraværende, utstedes ingen kapabilitet:
            # å gjette en release ville tilskrevet artefaktet en opprinnelse
            # serveren ikke kan bevise.
            opplasting = None
            oppdragsrad = conn.execute(
                "SELECT o.modul_id, ("
                "  SELECT string_agg(DISTINCT d.release_id, ',')"
                "    FROM moduldeployment d"
                "   WHERE d.modul_id = o.modul_id AND d.livslop = 'claiming'"
                "     AND d.kontraktversjon = o.kontraktversjon"
                "     AND d.kontrakt_hash = o.kontrakt_hash),"
                " o.kontraktversjon, o.kontrakt_hash, o.module_epoch"
                " FROM oppdrag o WHERE o.tenant=%s AND o.id=%s",
                (tenant, opp_id)).fetchone()
            if oppdragsrad is not None and oppdragsrad[0] is not None \
                    and oppdragsrad[1] is not None and "," not in oppdragsrad[1]:
                (o_modul, o_release, o_kv, o_khash, o_epoch) = oppdragsrad
                # Artefakttypen hentes fra REGISTERET, bundet til nøyaktig denne
                # modulen + kontrakten. Finnes ingen registrert type, utstedes
                # INGEN opplastingskapabilitet — og claimen lykkes fortsatt.
                # En modul som ikke skal laste opp, får ikke lov (port 22).
                #
                # Codex (P2): `LIMIT 1` plukket den alfabetisk FØRSTE typen
                # stille når kontrakten registrerer FLERE — svaret bar da en
                # kapabilitet for feil type uten at noe sa fra. Responsen har
                # ETT `opplasting`-felt, ikke en liste, og v1 har bevisst
                # ingen on-demand-utstedelse (se docstringen over) — samme
                # fail-closed regel som RELEASE-tvetydigheten over: er valget
                # tvetydig, utstedes ingen kapabilitet, ikke en gjettet én.
                typerader = conn.execute(
                    "SELECT artefakttype FROM artefakttype_register"
                    " WHERE eiermodul=%s AND kontraktversjon=%s"
                    "   AND kontrakt_hash=%s ORDER BY artefakttype LIMIT 2",
                    (o_modul, o_kv, o_khash)).fetchall()
                typerad = typerader[0] if len(typerader) == 1 else None
                if typerad is not None:
                    # Levetid = evidensfristen, ALDRI lengre (port 23). 017
                    # klemmer levetiden til [60, 3600]; er det under et minutt
                    # igjen til fristen, ville klemmen gitt et token som lever
                    # LENGER enn evidensen det er til for. Da utstedes ingen —
                    # å runde oppover her hadde vært å bryte grensen i det
                    # stille, og oppdraget er uansett tapt før opplastingen.
                    #
                    # Codex (P2): resttiden regnes av DATABASENS klokke, ikke
                    # API-vertens. `utsted_artefaktkapabilitet()` setter
                    # `utloper = now() + levetid` med basens `now()` og
                    # sammenligner ALDRI med evidensfristen selv; ligger
                    # API-vertens klokke etter basens, ble `igjen`
                    # overestimert og kapabiliteten levde forbi fristen den er
                    # hardt bundet av. `now()` er transaksjonens starttid og er
                    # den SAMME i begge kall — dette er én transaksjon — så
                    # `utloper = now() + min(igjen, 3600) <= evidensfrist`
                    # holder eksakt, ikke omtrentlig.
                    igjen = int(conn.execute(
                        "SELECT floor(extract(epoch FROM (%s::timestamptz"
                        " - now())))::INT", (ef,)).fetchone()[0])
                    if igjen >= 60:
                        opplasting_jti = secrets.token_hex(16)
                        # Epoch kontrolleres UNDER oppdragslåsen: dette kallet
                        # ligger i samme transaksjon som claimen, som holder
                        # raden. Endret epoch mellom claim og utstedelse gir
                        # ingen kapabilitet (port 24) — funksjonen matcher
                        # o.module_epoch og feiler.
                        orad = conn.execute(
                            "SELECT jti, utloper FROM utsted_artefaktkapabilitet("
                            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (tenant, opp_id, o_modul, o_release, o_kv, o_khash,
                             o_epoch, typerad[0], opplasting_jti,
                             min(igjen, 3600))).fetchone()
                        # Grensen HÅNDHEVES, den forutsettes ikke. Utledningen
                        # over gjør `utloper <= evidensfrist` til en identitet,
                        # men den identiteten hviler på 017s klemming — og en
                        # kapabilitet som overlever evidensen den er til for
                        # skal ikke kunne slippe ut fordi et ledd endret seg et
                        # annet sted. Går den likevel over, utstedes ingen
                        # `opplasting` i svaret: jti-en er da aldri utlevert og
                        # kan ikke innløses.
                        if orad is not None and orad[1] <= ef:
                            opplasting = {"jti": orad[0],
                                          "utloper": orad[1].isoformat(),
                                          "artefakttype": typerad[0]}
            conn.commit()
        except psycopg.Error as e:
            conn.rollback()
            tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                                   feiltype=type(e).__name__)
            return _feilsvar("db_utilgjengelig", rid)
        # Klartekst logges ALDRI. Sikkerhetsloggen får id-er, ikke innhold —
        # canary-testen i suiten planter en kjent verdi i payloaden og
        # feiler hvis den dukker opp i logg eller på disk.
        return kanonisk_json({
            "oppdrag_id": opp_id, "tenant": tenant, "unntak_id": unntak_id,
            "oppdragstype": oppdragstype, "handling": handling,
            "repair_operation_id": repair_id, "owner_claim_id": claim_id,
            "owner_generation": owner_gen,
            "utforelsesfrist": uf.isoformat(), "evidensfrist": ef.isoformat(),
            # Kvitteringskapabiliteten er modulens ENESTE adgang til
            # kvitteringsporten for DETTE oppdraget. Et langlivet modultoken
            # alene ville gitt adgang til å kvittere for hvilket som helst
            # oppdrag modulen noensinne har hatt.
            "kvittering_jti": kap[0],
            "kvittering_utloper": kap[1].isoformat(),
            # PR-015 §5: SEPARAT token, aldri utledet av kvitteringen og aldri
            # samme audience — `opplasting_jti` virker ikke som kvittering og
            # motsatt (port 21). `null` når oppdraget ikke har noen registrert
            # artefakttype: en modul som ikke skal laste opp, får ikke lov, og
            # claimen lykkes likevel (port 22).
            "opplasting": opplasting,
            "verification_generation": verifikasjonsgen,
            "payload": minimert, "request_id": rid}, 200,
            {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)


def _er_sha256_hex(verdi: object) -> bool:
    """En sha256 skrevet slik serveren selv skriver den: 64 små hex-siffer.

    Formen valideres FØR verdien når basen — en kvittering som påstår en hash i
    et annet format er feilformet, ikke et hashavvik.
    """
    return (isinstance(verdi, str) and len(verdi) == 64
            and all(c in "0123456789abcdef" for c in verdi))


def _resultathash(kvittering: dict) -> str:
    """Kanonisk hash over RESULTATET, ikke over hele kvitteringen.

    Skillet avgjør hva som er «samme kvittering»: to leveringer av det
    samme resultatet skal være idempotente selv om tidsstempel og signatur
    er nye, mens to ULIKE resultater må kollidere og bli en sikkerhetssak.
    Hashet vi hele kvitteringen, ville en re-post med nytt tidsstempel sett
    ut som et motstridende resultat.
    """
    # Codex (PR-014b §7): artefakt_id er en del av RESULTATET. Uten det ville to
    # ellers like kvitteringer som KUN skiller seg i artefakt_id hashet likt, og
    # den andre returnert 'idempotent' før artefakt-verifiseringen — så
    # motstridende artefaktbevis verken promoteres eller karantenesettes.
    # Codex P1: og `klartekst_sha256` — den ATTESTERTE hashen — hører til
    # resultatet på nøyaktig samme måte. To kvitteringer som påstår ulikt innhold
    # for samme oppdrag er motstridende evidens, ikke en idempotent re-post.
    kjerne_felt = {k: kvittering.get(k) for k in
                   ("oppdrag_id", "repair_operation_id", "resultat",
                    "ressurs_id")}
    # Codex: de to artefaktfeltene tas KUN med når kvitteringen faktisk bærer dem.
    # Skrev vi dem inn som eksplisitt null også for en kvittering uten artefakt,
    # ville hashen til ALLE kvitteringer fra før denne utrullingen endret seg:
    # deres lagrede `resultathash` er beregnet over den gamle firefelts-formen, og
    # en ellers BYTE-IDENTISK re-post innenfor evidensfristen ville derfor sett ut
    # som et nytt resultat og blitt klassifisert som `motstridende_kvittering` —
    # en sikkerhetssak utløst av selve oppgraderingen, ikke av controlleren.
    # Fraværende og null behandles likt: resten av koden måler artefaktveien på
    # `is not None`, så en eksplisitt null ER «ingen artefakt».
    for valgfritt in ("artefakt_id", "klartekst_sha256"):
        if kvittering.get(valgfritt) is not None:
            kjerne_felt[valgfritt] = kvittering[valgfritt]
    return hashlib.sha256(json.dumps(
        kjerne_felt, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _oppdrag_kvittering(tjeneste: Tjeneste, request: Request) -> Response:
    """Signert, ressursbundet resultatkvittering. Én tenantbundet transaksjon.

    Reglene fra v3-delta pkt. 3 og v4-delta pkt. 2-3, ordrett:
      * gyldig og innenfor utførelsesfristen, med GJELDENDE owner-fencing
        => oppdraget og saken kan avsluttes automatisk,
      * etter utførelsesfristen, eller fra utdatert generasjon => LAGRES
        som `sen_kvittering`, uten statusendring,
      * to ulike resultathasher for samme oppdrag => sikkerhetssak,
      * identisk kvittering => idempotent no-op,
      * ugyldig signatur => sikkerhetssak, ingen statusendring,
      * etter evidensfristen => avvises.

    Signaturen verifiseres i APP-LAGET mot modulens registrerte
    verifikatornøkkel. Nøkkelregisteret bor i app-state (lastet ved boot),
    aldri i databasen: en angriper med full DB-lesing skal ikke kunne lage
    en gyldig kvittering.
    """
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        auth = preauth(tjeneste, conn, request.headers.get("authorization"), rid)
        if auth is None or auth.kapabilitet is not None:
            tjeneste.logg.hendelse("token_ugyldig", rid)
            return _feilsvar("token_ugyldig", rid)
        if not _modulscope(auth):
            tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                   scope=ORDRESCOPE + "<prefiks>")
            return _feilsvar("scope_mangler", rid)

        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            kvittering = json.loads(raa.decode("utf-8"))
        except Exception:
            kvittering = None
        if not isinstance(kvittering, dict):
            tjeneste.logg.hendelse("request_feilformet", rid, auth.tenant)
            return _feilsvar("request_feilformet", rid)

        try:
            return _ingest_kvittering(tjeneste, conn, auth, kvittering, rid)
        except psycopg.Error as e:
            conn.rollback()
            tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                                   feiltype=type(e).__name__)
            return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


def _kvittering_alt_avvist(conn, tenant: str, unntak_id: int, oppdrag_id: int,
                           artefakt_id) -> bool:
    """Ble NØYAKTIG denne kvitteringen alt avvist og utfallet persistert?

    En avvist artefaktkvittering (promotering/bevaring feilet) skriver
    sikkerhetssaken `artefakt_ikke_verifisert` og lar oppdraget stå uavsluttet.
    Kapabiliteten husker derimot bare resultathashen — ikke at utfallet var en
    AVVISNING. Uten dette oppslaget ser en gjentakelse av den samme avviste
    kvitteringen ut som en vellykket 200 selv om jobben aldri ble fullført.

    Saken er persistert UAVHENGIG av artefakt-tilstand: den dekker også et
    FREMMED/IKKE-EKSISTERENDE artefakt der karantene/bevaring er en no-op og det
    derfor ikke finnes noen artefaktrad å lese utfallet av.

    ÉN funksjon, fordi utfallet må rekonstrueres på BEGGE veiene inn hit: den
    SEKVENSIELLE retryen (hashen er alt lagret) og KAPPLØPS-taperen (som blokkerte
    inne i `bruk_kvitteringskapabilitet` og våknet med `idempotent` etter at
    vinneren committet avvisningen sin). Var den bare implementert på den ene,
    fikk samme avviste kvittering 409 eller 200 avhengig av timing.
    """
    if artefakt_id is None:
        return False
    return conn.execute(
        "SELECT 1 FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        " AND hendelse='artefakt_ikke_verifisert'"
        " AND detalj->>'oppdrag_id' = %s AND detalj->>'artefakt_id' = %s",
        (tenant, unntak_id, str(oppdrag_id),
         str(artefakt_id))).fetchone() is not None


def _forbruk_kapabilitet(tjeneste: Tjeneste, conn, jti: str, ny_hash: str, *,
                         tenant: str, unntak_id: int, oppdrag_id: int,
                         rid: str, artefakt_id=None) -> Response | None:
    """Forbruker kapabiliteten, eller klassifiserer hvorfor vi ikke kunne.

    -> None betyr «kapabiliteten er VÅR, fortsett». Alt annet er et ferdig
    svar, og transaksjonen er avsluttet.

    Delt av BEGGE veiene — den avsluttende og sen-evidensveien. Det er ikke
    en stilsak: forrige runde viste hva som skjer når en regel bare er
    implementert i den ene av to grener, og runden før det viste det samme.
    Med én funksjon kan de to veiene ikke lenger gli fra hverandre.

    Klassifiseringen selv gjøres ATOMISK i databasen (se
    `bruk_kvitteringskapabilitet`). Å lese tilstanden herfra etterpå ville
    vært et nytt kappløp for å avgjøre utfallet av det første.
    """
    utfall = conn.execute("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                          (jti, ny_hash)).fetchone()[0]
    if utfall == "brukt":
        return None

    if utfall == "idempotent":
        # Vi tapte kappløpet mot en IDENTISK kvittering. Vinneren har
        # skrevet evidensraden; vi skal ikke skrive en til.
        #
        # Codex: men vinneren kan ha AVVIST den. Vi blokkerte på kapabilitetsraden
        # inne i `bruk_kvitteringskapabilitet` til vinneren committet — utfallet
        # står altså persistert og lesbart (READ COMMITTED: setningen under tar et
        # ferskt snapshot). Uten oppslaget fikk den ene av to identiske avviste
        # kvitteringer 409 og den andre 200, avgjort av timing alene. Samme
        # rekonstruksjon som den sekvensielle retryen, samme funksjon.
        avvist = _kvittering_alt_avvist(conn, tenant, unntak_id, oppdrag_id,
                                        artefakt_id)
        conn.rollback()
        if avvist:
            tjeneste.logg.hendelse("kvittering_konflikt", rid, tenant,
                                   oppdrag_id=oppdrag_id, kapplop=True)
            return _feilsvar("kvittering_konflikt", rid)
        return kanonisk_json({"status": "idempotent",
                              "oppdrag_id": oppdrag_id, "request_id": rid},
                             200, {"x-request-id": rid})

    if utfall == "konflikt":
        # To ULIKE resultater levert samtidig. Uten denne grenen forsvant
        # forsøket på motstridende evidens som et generisk auth-avvik —
        # altså nøyaktig den hendelsen sikkerhetssaken finnes for.
        _sikkerhetssak_kvittering(conn, tenant, unntak_id,
                                  "motstridende_kvittering",
                                  {"kilde": "kapplop", "ny": ny_hash,
                                   "oppdrag_id": oppdrag_id}, rid)
        # Codex P2: også kappløps-TAPEREN navngir et artefakt, og det er nettopp
        # evidensen sikkerhetssaken trenger. Hash-konfliktveien bevarer det alt;
        # denne atomiske DB-konfliktgrenen returnerte før den nådde bevaringen, så
        # taperens artefakt stod igjen `staged` og fikk cipherteksten nullet av
        # oppryddingen. Karantene i SAMME commit. No-op for fremmed/ikke-staged.
        if artefakt_id is not None:
            conn.execute("SELECT karantenesett_artefakt(%s,%s,%s)",
                         (artefakt_id, tenant, oppdrag_id))
        conn.commit()
        tjeneste.logg.hendelse("kvittering_konflikt", rid, tenant,
                               oppdrag_id=oppdrag_id, kapplop=True)
        return _feilsvar("kvittering_konflikt", rid)

    conn.rollback()
    tjeneste.logg.hendelse("kapabilitet_ugyldig", rid, tenant,
                           oppdrag_id=oppdrag_id, utfall=utfall)
    return _feilsvar("kapabilitet_ugyldig", rid)


def _ingest_kvittering(tjeneste: Tjeneste, conn, auth: Autentisert,
                       kvittering: dict, rid: str) -> Response:
    """Innløser kvitteringskapabiliteten, verifiserer signaturen, og
    committer alt i ÉN tenantbundet transaksjon.

    Rekkefølgen er bindende, og hvert steg har en grunn:

      1. KAPABILITETEN FØRST. Den er serverbundet og gir tenant, oppdrag,
         modul og owner-fencing. Uten den ville tenanten kommet fra
         kroppen — altså fra den som skriver kvitteringen.
      2. SIGNATUREN. Er den ugyldig, er feltene ikke til å stole på, og da
         er det meningsløst å sammenligne dem med noe. Samme rekkefølge og
         samme begrunnelse som attestasjonsporten i `kjerne._flyt` steg 4.
      3. FRISTENE. Evidensfristen avviser; utførelsesfristen avgjør bare om
         kvitteringen kan LUKKE noe.
      4. RESULTATET. Identisk => idempotent. Motstridende => sikkerhetssak,
         aldri «siste vinner».

    Merk hva som IKKE lagres på oppdragsraden: en kvittering som ikke kan
    avslutte. Den går i historikken som `sen_kvittering`. Skrev vi den til
    `oppdrag.kvittering`, ville kolonnelåsen (kvitteringen er uforanderlig)
    gjort det umulig for den NYE eieren å levere sin — altså ville en
    utdatert kvittering blokkert den gjeldende.
    """
    from policy_validator import attestering

    jti = kvittering.get("kvittering_jti")
    # Utførelseskvitteringen er flat; verifikasjonskvitteringen legger
    # bindingene i den SIGNERTE konvolutten, der de hører hjemme — et
    # oppdrag_id utenfor signaturen er en påstand ingen har skrevet under
    # på. Begge former oppgir oppdraget, bare på hver sin plass.
    oppgitt_oppdrag = kvittering.get("oppdrag_id")
    if oppgitt_oppdrag is None:
        oppgitt_oppdrag = (kvittering.get("konvolutt") or {}).get("oppdrag_id") \
            if isinstance(kvittering.get("konvolutt"), dict) else None
    if not isinstance(jti, str) or not jti \
            or not isinstance(oppgitt_oppdrag, int):
        return _feilsvar("request_feilformet", rid)

    # 1. Kapabiliteten. `modul_id` sammenlignes inne i funksjonen, så en
    #    annen modul kan ikke innløse en kapabilitet den har fått tak i.
    kap = conn.execute(
        "SELECT tenant, oppdrag_id, owner_claim_id, owner_generation, status,"
        " resultathash FROM innlos_kvitteringskapabilitet(%s, %s)",
        (jti, auth.rolle)).fetchone()
    if kap is None:
        conn.rollback()
        tjeneste.logg.hendelse("kapabilitet_ugyldig", rid, auth.tenant)
        return _feilsvar("kapabilitet_ugyldig", rid)
    (tenant, oppdrag_id, kap_claim, kap_gen, kap_status,
     kap_hash) = kap
    if oppgitt_oppdrag != oppdrag_id:
        # Kapabiliteten er bundet til ETT oppdrag. En kropp som peker på et
        # annet er enten en feil eller et forsøk — begge avvises likt.
        conn.rollback()
        tjeneste.logg.hendelse("kapabilitet_ugyldig", rid, tenant,
                               oppgitt=oppgitt_oppdrag, bundet=oppdrag_id)
        return _feilsvar("kapabilitet_ugyldig", rid)

    # Kontekst FØRST i den autentiserte transaksjonen, og tenanten kommer
    # fra KAPABILITETEN — aldri fra kvitteringskroppen.
    sett_kontekst(conn, tenant, auth.aktor, rid)

    rad = conn.execute(
        "SELECT o.status, o.owner_claim_id, o.owner_generation,"
        " o.utforelsesfrist, o.evidensfrist, o.resultathash, o.unntak_id,"
        " o.oppdragstype"
        "  FROM oppdrag o WHERE o.tenant=%s AND o.id=%s",
        (tenant, oppdrag_id)).fetchone()
    if rad is None:
        conn.rollback()
        return _feilsvar("kapabilitet_ugyldig", rid)
    (status, owner_claim, owner_gen, uf, ef, lagret_hash, unntak_id,
     oppdragstype) = rad

    # PR-007: verifikasjonsoppdrag har sin EGEN ingest. Skillet er ikke
    # kosmetisk — en utførelseskvittering skal aldri kunne bære en
    # attestasjon, og en verifikasjonskvittering skal aldri kunne sette et
    # forretningsresultat.
    if oppdragstype == "verifikasjon":
        return _ingest_verifikasjon(tjeneste, conn, auth, kvittering, rid,
                                    tenant=tenant, oppdrag_id=oppdrag_id,
                                    unntak_id=unntak_id, jti=jti,
                                    owner_claim=kap_claim, owner_gen=kap_gen)

    # En utførelseskvittering som bærer attestasjonsfelt prøver å levere
    # bevis gjennom feil dør (v2-delta pkt. 5).
    if not oppdragskontrakt.er_utforelseskvittering(kvittering):
        conn.rollback()
        tjeneste.logg.hendelse("kvittering_signatur_ugyldig", rid, tenant,
                               oppdrag_id=oppdrag_id, grunn="attestasjonsfelt")
        return _feilsvar("kvittering_signatur_ugyldig", rid)

    # 2. Signaturen.
    if not attestering.verifiser(kvittering, tjeneste.nokler):
        conn.rollback()
        tjeneste.logg.hendelse("kvittering_signatur_ugyldig", rid, tenant,
                               oppdrag_id=oppdrag_id)
        return _feilsvar("kvittering_signatur_ugyldig", rid)

    naa = datetime.now(timezone.utc)
    if naa > ef:
        conn.rollback()
        return _feilsvar("kvittering_for_sen", rid)

    # Codex: valider artefakt_id som UUID FØR kapabiliteten forbrukes eller den
    # uuid-typede artefakt-kolonnen spørres. En gyldig signert kvittering med en
    # ikke-UUID artefakt_id ville ellers nådd `WHERE artefakt_id=%s` og fått
    # PostgreSQL til å kaste InvalidTextRepresentation — rapportert som
    # db_utilgjengelig (som om basen var nede) i stedet for request_feilformet.
    #
    # Codex: valideringen må treffe den verdien som FAKTISK bindes. `uuid.UUID(
    # str(x))` godtok `urn:uuid:...`, `{...}`, versaler og — etter str()-en — et
    # 32-sifret JSON-TALL, men kastet det parsede resultatet: nedenfor ble
    # ORIGINALEN bundet, og da avviste PostgreSQL urn-formen eller forsøkte en
    # ugyldig uuid-mot-numeric-sammenligning — nøyaktig den feilklassifiseringen
    # (db_utilgjengelig) valideringen var der for å fjerne.
    #
    # Vi KREVER den kanoniske teksten i stedet for å normalisere, på samme måte
    # som `_er_sha256_hex` krever lowercase hex: kvitteringen lagres ORDRETT i
    # `oppdrag.kvittering` som signert evidens, og en serverside-omskriving ville
    # gjort den arkiverte kvitteringen ulik den som faktisk ble signert. Kravet
    # er heller ikke strengt for en ekte controller — opplastingssvaret returnerer
    # `str(aid)`, altså nøyaktig den kanoniske formen den skal signere.
    raa_art = kvittering.get("artefakt_id")
    if raa_art is not None:
        try:
            kanonisk_art = str(uuid.UUID(raa_art))
        except (ValueError, AttributeError, TypeError):
            kanonisk_art = None
        if kanonisk_art != raa_art:
            conn.rollback()
            tjeneste.logg.hendelse("request_feilformet", rid, tenant,
                                   oppdrag_id=oppdrag_id, grunn="artefakt_id")
            return _feilsvar("request_feilformet", rid)

    # Codex P1: en artefaktkvittering må BÆRE hashen den attesterer. Uten et
    # signert `klartekst_sha256` leste promoteringen hashen fra selve artefaktet
    # og sendte den tilbake til `promoter_artefakt` — sammenligningen der var en
    # tautologi (samme radverdi mot seg selv). Signaturen sa da bare HVILKET
    # artefakt som gjaldt, aldri HVA det inneholdt: den som holdt
    # opplastingstokenet og kapabilitets-jti-en kunne lastet opp en vilkårlig
    # rapport og fått den promotert som attestert evidens. Hashen valideres FØR
    # kapabiliteten forbrukes, på samme måte som artefakt_id.
    raa_hash = kvittering.get("klartekst_sha256")
    if raa_art is not None and not _er_sha256_hex(raa_hash):
        conn.rollback()
        tjeneste.logg.hendelse("request_feilformet", rid, tenant,
                               oppdrag_id=oppdrag_id, grunn="klartekst_sha256")
        return _feilsvar("request_feilformet", rid)

    ny_hash = _resultathash(kvittering)

    # 4. Idempotens og konflikt — målt mot BEGGE kilder. Kapabiliteten
    #    husker hva DEN ble brukt til; oppdraget husker hva som avsluttet
    #    det. En re-post treffer den første, en annen utfører den andre.
    for kilde, hash_ in (("kapabilitet", kap_hash), ("oppdrag", lagret_hash)):
        if hash_ is None:
            continue
        if hash_ == ny_hash:
            # Codex: skill en idempotent SUKSESS fra en idempotent AVVISNING
            # (se `_kvittering_alt_avvist` — samme rekonstruksjon som
            # kappløpsveien i `_forbruk_kapabilitet`).
            if _kvittering_alt_avvist(conn, tenant, unntak_id, oppdrag_id,
                                      kvittering.get("artefakt_id")):
                conn.rollback()
                return _feilsvar("kvittering_konflikt", rid)
            conn.rollback()
            return kanonisk_json({"status": "idempotent",
                                  "oppdrag_id": oppdrag_id,
                                  "request_id": rid}, 200,
                                 {"x-request-id": rid})
        _sikkerhetssak_kvittering(conn, tenant, unntak_id,
                                  "motstridende_kvittering",
                                  {"kilde": kilde, "lagret": hash_,
                                   "ny": ny_hash}, rid)
        # Samme bevaringsregel som den sene veien: sikkerhetssaken er skrevet,
        # og artefaktet det motstridende resultatet påberoper seg er nettopp
        # det etterforskningen trenger. Karantene = retained (ryddes aldri).
        # No-op for et alt promotert/fremmed artefakt.
        strid_artefakt = kvittering.get("artefakt_id")
        if strid_artefakt is not None:
            conn.execute("SELECT karantenesett_artefakt(%s,%s,%s)",
                         (strid_artefakt, tenant, oppdrag_id))
        conn.commit()
        tjeneste.logg.hendelse("kvittering_konflikt", rid, tenant,
                               oppdrag_id=oppdrag_id)
        return _feilsvar("kvittering_konflikt", rid)

    # 3b. Owner-fencing: kapabilitetens generasjon mot oppdragets GJELDENDE.
    #     En kapabilitet fra en utdatert generasjon er fortsatt gyldig
    #     EVIDENS, men den vinner aldri over den nye eieren.
    gjeldende = (kap_claim == owner_claim and kap_gen == owner_gen
                 and status == "plukket")
    kan_avslutte = gjeldende and naa <= uf and kap_status == "utstedt"

    if not kan_avslutte:
        # KAPABILITETEN FORBRUKES OGSÅ HER (Codex P1, runde 2).
        #
        # Første utgave skrev bare historikkraden og lot kapabiliteten stå
        # `utstedt` med `resultathash = NULL`. Konsekvensen var større enn
        # duplikatlogging: samme jti kunne poste ubegrenset mange sene
        # kvitteringer, og siden ingenting hadde lagret den FØRSTE hashen,
        # ble et MOTSTRIDENDE resultat lagret som ordinær evidens i stedet
        # for å bli en sikkerhetssak.
        #
        # Reglene «identisk kvittering => idempotent no-op» og «to ulike
        # resultathasher => sikkerhetssak» gjaldt altså bare den
        # avsluttende veien — nettopp ikke stale-generation/etter-frist-
        # veien de er til for. Samme funnfamilie som resten av dette
        # prosjektet: porten fantes, men dekket ikke det den ga inntrykk av.
        #
        # Forbruket skjer i SAMME commit som evidensraden. Statusen på
        # oppdraget og saken røres ikke — en sen kvittering er evidens, og
        # skal aldri avslutte noe.
        svar = _forbruk_kapabilitet(tjeneste, conn, jti, ny_hash,
                                    tenant=tenant, unntak_id=unntak_id,
                                    oppdrag_id=oppdrag_id, rid=rid,
                                    artefakt_id=kvittering.get("artefakt_id"))
        if svar is not None:
            # Taperen av kappløpet skriver INGEN evidensrad. Den ville vært
            # den andre raden for samme jti — og om utfallet var idempotent
            # eller konflikt, avgjøres atomisk i databasen, ikke her.
            return svar
        # PR-014b §7 (Codex): den sene kvitteringen GODTAS som evidens — da må
        # artefaktet den peker på overleve OG faktisk stemme. `bevar_artefakt`
        # alene krevde ingen matchende rad og sammenlignet ingen hash: en gyldig
        # signert kvittering som navnga et ikke-eksisterende/fremmed artefakt,
        # eller påsto feil hash, ble ellers akseptert (202) med en payload som ikke
        # kan gjenopprettes/verifiseres. Valider som promoteringsveien (tenant/
        # oppdrag/signert hash) FØR aksept. `FOR UPDATE` serialiserer også mot
        # oppryddingen: har rydd nettopp nullet raden i race-en rundt evidensfristen,
        # ser vi den ikke som gjenopprettbar og klassifiserer konflikt i stedet for
        # falsk aksept. `bevart` er retained/terminalt; idempotent hvis alt bevart.
        sen_artefakt = kvittering.get("artefakt_id")
        if sen_artefakt is not None:
            # bevar_artefakt validerer (tenant/oppdrag/signert hash), låser raden
            # (serialiserer mot oppryddingen) og bevarer den atomisk. 'ugyldig' =
            # fremmed/ikke-eksisterende/feil-hash/alt-nullet → sikkerhetskonflikt,
            # ikke falsk aksept. Runtime kan ikke låse artefakt selv (kun SELECT).
            utfall = conn.execute(
                "SELECT bevar_artefakt(%s,%s,%s,%s)",
                (sen_artefakt, tenant, oppdrag_id,
                 kvittering.get("klartekst_sha256"))).fetchone()[0]
            if utfall == "ugyldig":
                # Brenningen fra _forbruk står ved lag; klassifiser som
                # sikkerhetskonflikt (som promoteringsfeil) og commit den.
                _sikkerhetssak_kvittering(
                    conn, tenant, unntak_id, "artefakt_ikke_verifisert",
                    {"oppdrag_id": oppdrag_id, "artefakt_id": str(sen_artefakt)},
                    rid)
                conn.commit()
                tjeneste.logg.hendelse("artefakt_promotering_avvist", rid, tenant,
                                       oppdrag_id=oppdrag_id, art="sikkerhet")
                return _feilsvar("kvittering_konflikt", rid)
        conn.execute(
            "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
            " request_id, detalj) VALUES (%s,%s,'sen_kvittering',%s,%s,%s)",
            (tenant, unntak_id, auth.aktor, rid,
             json.dumps({"oppdrag_id": oppdrag_id,
                         "gjeldende_fencing": gjeldende,
                         "etter_utforelsesfrist": naa > uf,
                         "resultathash": ny_hash}, ensure_ascii=False)))
        conn.commit()
        return kanonisk_json({"status": "lagret_uten_statusendring",
                              "oppdrag_id": oppdrag_id, "request_id": rid},
                             202, {"x-request-id": rid})

    # Forbruk av kapabiliteten i SAMME commit som statusskiftet. Feiler
    # den, har noen andre rukket å bruke den, og da skal ingenting skje.
    svar = _forbruk_kapabilitet(tjeneste, conn, jti, ny_hash, tenant=tenant,
                                unntak_id=unntak_id, oppdrag_id=oppdrag_id,
                                rid=rid, artefakt_id=kvittering.get("artefakt_id"))
    if svar is not None:
        return svar

    # PR-014b §7: refererer kvitteringen et artefakt, PROMOTERES det i SAMME
    # transaksjon som statusskiftet — «kvittering godtas aldri før artefaktet er
    # varig lagret og verifisert» (pkt. 6). artefakt_id er signert (del av
    # kvitteringen). Promoteringen verifiserer tenant/oppdrag/release/epoch/hash;
    # feiler den (epoch-drift eller bindingsavvik), avsluttes INGENTING —
    # artefaktet bevares (opprydding rører det aldri), og kvitteringen
    # karantenesettes som sikkerhetssak (pkt. 8). Legacy-kvitteringer uten
    # artefakt_id er helt uendret.
    art_id = kvittering.get("artefakt_id")
    if art_id is not None:
        art = conn.execute(
            "SELECT release_id FROM artefakt"
            " WHERE artefakt_id=%s AND tenant=%s AND oppdrag_id=%s",
            (art_id, tenant, oppdrag_id)).fetchone()
        # Codex P1: MODUL-LÅSEN (delt) FØR epoch leses, og den holdes til commit.
        # Uten den var epoch-sammenligningen under et ulåst punktavlesningsbilde:
        # `noddeaktiver_modul` serialiserer epoch-endringene sine på `modul:<id>`,
        # så et nødstopp som committet ETTER denne SELECT-en, men FØR
        # kvitteringstransaksjonen, lot likheten stå igjen sann — og den utgjerdede
        # controlleren promoterte artefaktet og avsluttet oppdraget etter
        # nødstoppen likevel. Delt lås (samme form som claim_neste_oppdrag):
        # kvitteringer serialiseres mot nødstopp/statusoverganger, men ikke mot
        # hverandre. Låsen er en xact-lås — den slippes først i commit/rollback,
        # så nødstoppet venter på HELE denne transaksjonen.
        oppdragsmodul = conn.execute(
            "SELECT modul_id, module_epoch FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (tenant, oppdrag_id)).fetchone()
        opp_epoch = oppdragsmodul[1]
        if oppdragsmodul[0] is not None:
            conn.execute(
                "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))",
                ("modul:" + oppdragsmodul[0],))
        # Sammenlign mot modulens GJELDENDE epoch, ikke bare den claim-tid-
        # stemplede på oppdraget. `noddeaktiver_modul` løfter modulhode.module_epoch;
        # oppdragets stemplede epoch er derimot frosset. En artefakt+kvittering fra
        # en NØDSTOPPET controller ville ellers fortsatt promotert (stemplet ==
        # stemplet) og avsluttet oppdraget etter nødstoppen. Er de ulike (eller er
        # oppdraget ikke modulbundet i det hele tatt), gjerdes controlleren ut →
        # ingen promotering → karantene.
        naa_epoch = conn.execute(
            "SELECT module_epoch FROM modulhode WHERE modul_id=%s",
            (oppdragsmodul[0],)).fetchone()
        promotert = (art is not None and naa_epoch is not None
                     and opp_epoch == naa_epoch[0])
        if promotert:
            try:
                # SAVEPOINT: en promoteringsfeil (epoch-drift/bindingsavvik) må
                # IKKE rulle tilbake kapabilitet-brenningen + resultathashen som
                # _forbruk_kapabilitet nettopp skrev. Codex P2: en full rollback
                # her angret brenningen FØR sikkerhetssaken ble committet i en ny
                # transaksjon, så den samme ugyldige kvitteringen kunne spilles
                # om til fristen, og en ANNEN kvittering med samme jti ble aldri
                # klassifisert mot den første resultathashen.
                with conn.transaction():
                    # `raa_hash` er den SIGNERTE hashen fra kvitteringen — ikke
                    # artefaktets egen (Codex P1). Sammenligningen i
                    # `promoter_artefakt` er dermed en ekte attestasjon: stemmer
                    # ikke det signeren skrev under på med det som faktisk ligger
                    # lagret, promoteres ingenting.
                    conn.execute("SELECT promoter_artefakt(%s,%s,%s,%s,%s,%s,%s)",
                                 (art_id, tenant, oppdrag_id, art[0], opp_epoch,
                                  raa_hash, auth.aktor))
            except psycopg.errors.InvalidParameterValue:
                promotert = False   # epoch-drift/bindingsavvik (kun savepoint rullet)
        if not promotert:
            # Brenningen + resultathashen står ved lag (bare savepointen ble
            # rullet). Codex §7 pkt. 8: bevar det uverifiserte artefaktet for
            # etterforskning (karantene → oppryddingen rører det aldri) OG commit
            # brenning + karantene + sikkerhetssak i SAMME transaksjon. No-op for
            # et FREMMED artefakt (ikke bundet til dette oppdraget).
            conn.execute("SELECT karantenesett_artefakt(%s,%s,%s)",
                         (art_id, tenant, oppdrag_id))
            _sikkerhetssak_kvittering(
                conn, tenant, unntak_id, "artefakt_ikke_verifisert",
                {"oppdrag_id": oppdrag_id, "artefakt_id": str(art_id)}, rid)
            conn.commit()
            tjeneste.logg.hendelse("artefakt_promotering_avvist", rid, tenant,
                                   oppdrag_id=oppdrag_id, art="sikkerhet")
            return _feilsvar("kvittering_konflikt", rid)

    vellykket = kvittering.get("resultat") == "utfort"
    conn.execute(
        "UPDATE oppdrag SET kvittering=%s, kvittering_signatur=%s,"
        " resultathash=%s, status=%s WHERE tenant=%s AND id=%s",
        (json.dumps(kvittering, ensure_ascii=False),
         (kvittering.get("signatur") or {}).get("verdi"), ny_hash,
         "utfort" if vellykket else "feilet", tenant, oppdrag_id))
    conn.execute(
        "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
        " request_id, detalj) VALUES (%s,%s,'kvittering',%s,%s,%s)",
        (tenant, unntak_id, auth.aktor, rid,
         json.dumps({"oppdrag_id": oppdrag_id,
                     "resultat": kvittering.get("resultat"),
                     "ressurs_id": kvittering.get("ressurs_id")},
                    ensure_ascii=False)))
    conn.execute(
        "UPDATE unntak SET status=%s WHERE tenant=%s AND id=%s"
        "   AND status='venter_utførelse'",
        ("løst" if vellykket else "manuell", tenant, unntak_id))
    conn.commit()
    return kanonisk_json({"status": "utfort" if vellykket else "feilet",
                          "oppdrag_id": oppdrag_id, "unntak_id": unntak_id,
                          "request_id": rid}, 200, {"x-request-id": rid})


def _sikkerhetssak_kvittering(conn, tenant: str, unntak_id: int,
                              hendelse: str, detalj: dict, rid: str) -> None:
    """Historikkrad for kvitteringsavvik. INGEN statusendring.

    v3-delta pkt. 3 er tydelig: et motstridende resultat skal ikke avgjøre
    noe. Det skal SES. En automatisk statusendring her ville betydd at den
    som klarer å sende to ulike kvitteringer bestemmer utfallet.
    """
    conn.execute(
        "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
        " request_id, detalj) VALUES (%s,%s,%s,'kvitteringsport',%s,%s)",
        (tenant, unntak_id, hendelse, rid,
         json.dumps(detalj, ensure_ascii=False)))


# ---------------------------------------------------------------------------
# PR-014b §7: artefakt-opplasting
# ---------------------------------------------------------------------------

def _artefakt_upload(tjeneste: Tjeneste, request: Request) -> Response:
    """Controlleren laster opp en lukket rapport med en EGEN kapabilitet.

    Tenanten kommer fra KAPABILITETEN, aldri fra body. Serveren kanoniserer
    (JCS) og hasher rapporten selv — modulens egen hash-påstand finnes ikke i
    kontrakten. Rapporten krypteres med tenant-DEK og lagres `staged`; artefaktet
    promoteres senere i kvittering-ingesten (samme tx som statusovergangen).
    Idempotent på (kapabilitet_jti, serverhash); samme jti + ANNET dokument →
    motstridende evidens.
    """
    from policy_validator import jcs
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        auth = preauth(tjeneste, conn, request.headers.get("authorization"), rid)
        if auth is None or auth.kapabilitet is not None:
            tjeneste.logg.hendelse("token_ugyldig", rid)
            return _feilsvar("token_ugyldig", rid)
        if not tjeneste.rate.slipp_gjennom(auth.token_id):
            tjeneste.logg.hendelse("rate_grense", rid, auth.tenant)
            return _feilsvar("rate_grense", rid)
        if "artifacts:upload" not in auth.scopes:
            tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                   scope="artifacts:upload")
            return _feilsvar("scope_mangler", rid)

        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            kropp = json.loads(raa.decode("utf-8"))
            jti = kropp["kapabilitet_jti"]
            rapport = kropp["rapport"]
        except (ValueError, KeyError, AttributeError, TypeError,
                RecursionError):
            # Codex: `json.loads` er REKURSIV. Et syntaktisk gyldig, dypt nøstet
            # dokument på noen få kilobyte (≈2 000 nivåer) treffer
            # rekursjonsgrensen og kaster RecursionError — en RuntimeError, ikke
            # en ValueError. Uten den her slapp den ut som generisk 500 i stedet
            # for det dokumenterte `request_feilformet`. Dybde er klientinput.
            return _feilsvar("request_feilformet", rid)
        if not isinstance(jti, str) or not isinstance(rapport, dict):
            return _feilsvar("request_feilformet", rid)

        # Innløs kapabiliteten — KUN for den holdende modulen (auth.rolle).
        bind = conn.execute(
            "SELECT tenant, oppdrag_id, release_id, kontraktversjon,"
            " kontrakt_hash, module_epoch, artefakttype"
            "  FROM innlos_artefaktkapabilitet(%s, %s)",
            (jti, auth.rolle)).fetchone()
        if bind is None:
            conn.rollback()
            tjeneste.logg.hendelse("kapabilitet_ugyldig", rid)
            return _feilsvar("kapabilitet_ugyldig", rid)
        (tenant, opp_id, release_id, kontraktversjon, kontrakt_hash,
         module_epoch, artefakttype) = bind

        # Tenant fra kapabiliteten. Server-beregnet JCS-hash + størrelse.
        sett_kontekst(conn, tenant, auth.aktor, rid)
        try:
            kanon = jcs.kanoniske_bytes(rapport)
        except (jcs.Ikkekanoniserbar, UnicodeEncodeError, RecursionError):
            # Codex: en escaped ensom surrogate (f.eks. "\ud800") slipper gjennom
            # json.loads, men kanoniseringen kan da kaste UnicodeEncodeError i
            # stedet for Ikkekanoniserbar. Begge er feilformet klientinput.
            # RecursionError er belte-og-seler: `jcs.MAKS_DYBDE` avviser dyp
            # nøsting som `Ikkekanoniserbar` FØR stacken renner over, men skulle
            # kalleren allerede stå dypt, er også overflyten feilformet input —
            # aldri en 500.
            conn.rollback()
            return _feilsvar("request_feilformet", rid)
        storrelse = len(kanon)
        if storrelse > 1048576:
            conn.rollback()
            return _feilsvar("body_for_stor", rid)
        klartekst_sha256 = hashlib.sha256(kanon).hexdigest()

        try:
            key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
        except psycopg.Error:
            raise
        except Exception as e:
            conn.rollback()
            return _feilsvar("tenantnokkel_mangler", rid)
        ct, nonce = kryptering.krypter(dek, rapport, tenant, key_id)

        try:
            aid = conn.execute(
                "SELECT lagre_artefakt_staged(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s)",
                (tenant, opp_id, artefakttype, auth.rolle, release_id,
                 kontraktversjon, kontrakt_hash, module_epoch, storrelse,
                 klartekst_sha256, ct, nonce, key_id, jti)).fetchone()[0]
        except psycopg.errors.UniqueViolation:
            # Samme jti + ANNET kanonisk dokument → motstridende evidens.
            conn.rollback()
            tjeneste.logg.hendelse("artefakt_konflikt", rid, tenant,
                                   art="sikkerhet")
            return _feilsvar("idempotenskonflikt", rid)
        except psycopg.errors.InvalidParameterValue:
            # Codex: kapabilitetsVALIDERINGEN (ukjent/utløpt/feilet kapabilitet,
            # bindingsavvik, manglende payload) reiser `invalid_parameter_value` —
            # den er en normal grensetilstand for en kortlevd kapabilitet, ikke en
            # basefeil. Uten denne grenen falt særlig utløps-kappløpet (kapabiliteten
            # utløper etter `innlos_artefaktkapabilitet` har lest den, men før
            # radlåsen i `lagre_artefakt_staged`) ned i catch-all-en under og ble
            # rapportert som `db_utilgjengelig` — altså en driftshendelse med
            # tilhørende retry-atferd og overvåkingsstøy for noe som bare var en
            # ugyldig kapabilitet. Catch-all-en er reservert for EKTE basefeil.
            conn.rollback()
            tjeneste.logg.hendelse("kapabilitet_ugyldig", rid, tenant)
            return _feilsvar("kapabilitet_ugyldig", rid)

        # lagre_artefakt_staged validerer OG forbruker kapabiliteten atomisk
        # (Codex 016:502) — ingen separat bruk-kall lenger.
        conn.commit()
        # Server-beregnet hash returneres (modulen binder den i resultatkvitteringen).
        return kanonisk_json({"artefakt_id": str(aid),
                              "klartekst_sha256": klartekst_sha256,
                              "request_id": rid}, 200, {"x-request-id": rid})
    except psycopg.Error:
        # Codex: DB-feil (transient eller en constraint fra en foreldet binding)
        # skal følge det kanoniske feilkontraktet, ikke lekke som generisk 500.
        try:
            conn.rollback()
        except psycopg.Error:
            pass
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


# ---------------------------------------------------------------------------
# PR-007: verifikasjonsingest
# ---------------------------------------------------------------------------

def _ingest_verifikasjon(tjeneste: Tjeneste, conn, auth: Autentisert,
                         kvittering: dict, rid: str, *, tenant: str,
                         oppdrag_id: int, unntak_id: int, jti: str,
                         owner_claim: str, owner_gen: int) -> Response:
    """Lagrer HELE settet av verifiserte bevis. Utfører aldri noe.

    Rekkefølgen, og hvorfor hvert steg må ligge der det ligger:

      1. **FORM.** Konvolutten må ha nøyaktig den deklarerte formen —
         ukjent felt er en feil, ikke stillhet.
      2. **SIGNATUR**, i APP-LAGET der nøkkelregisteret bor. Databasen ser
         aldri en nøkkel. `attestering.verifiser` slår opp nøkkelen på
         konvoluttens `verifikator`-felt, så en selvrapportert identitet
         som ikke eier nøkkelen faller her.
      3. **AKTIV AUTORITET** (Scope v2 pkt. 2). Verifikatoren må FORTSATT
         være betrodd for HVERT vilkår i settet, målt mot aktiv policy —
         ikke mot snapshotet. Snapshotet beviser forsøket; en tilbakekalt
         fullmakt må fanges på nåtid.
      4. **KRYPTERING** per attestasjon, med `integritet_hash` over
         CIPHERTEXT. En hash over klartekst ville vært et orakel: en
         attestasjon har få utfall, og en dump kunne gjettet innholdet.
      5. **DATABASEN AVGJØR.** `registrer_verifikasjonsbevis` er eneste
         skrivevei og eneste serialiseringspunkt (GO-vilkår V1).
    """
    from policy_validator import attestering

    konvolutt = kvittering.get("konvolutt")
    formfeil = oppdragskontrakt.valider_verifikasjonskvittering(konvolutt)
    if formfeil:
        conn.rollback()
        tjeneste.logg.hendelse("request_feilformet", rid, tenant,
                               oppdrag_id=oppdrag_id, feil=formfeil[:3])
        return _feilsvar("request_feilformet", rid)

    if not attestering.verifiser(konvolutt, tjeneste.nokler):
        conn.rollback()
        tjeneste.logg.hendelse("kvittering_signatur_ugyldig", rid, tenant,
                               oppdrag_id=oppdrag_id)
        return _feilsvar("kvittering_signatur_ugyldig", rid)

    if konvolutt.get("nokkel_id") != (konvolutt.get("signatur") or {}).get(
            "nokkel_id"):
        # Toppfeltet er det vi LAGRER som bevisets nøkkel-id; signaturens er
        # den vi faktisk verifiserte med. Spriker de, ville revisjonssporet
        # pekt på en annen nøkkel enn den som beviste noe.
        conn.rollback()
        return _feilsvar("kvittering_signatur_ugyldig", rid)

    if konvolutt.get("tenant_id") != tenant \
            or konvolutt.get("oppdrag_id") != oppdrag_id \
            or konvolutt.get("unntak_id") != unntak_id:
        conn.rollback()
        tjeneste.logg.hendelse("kvittering_signatur_ugyldig", rid, tenant,
                               oppdrag_id=oppdrag_id, grunn="binding")
        return _feilsvar("kvittering_signatur_ugyldig", rid)

    # --- 3. Aktiv autoritet, per vilkår ---------------------------------
    sett_kontekst(conn, tenant, auth.aktor, rid)
    try:
        policy_ref = conn.execute(
            "SELECT r.policy_id, u.handling FROM revisjonslogg r JOIN unntak u"
            "   ON u.tenant=r.tenant AND u.loggpost_id=r.id"
            " WHERE u.tenant=%s AND u.id=%s", (tenant, unntak_id)).fetchone()
        from policy_validator.engine import les_policyref
        ref = les_policyref(policy_ref[0]) if policy_ref else None
        prad = conn.execute(
            "SELECT innhold FROM policyer WHERE tenant=%s AND policy_id=%s"
            "   AND aktiv", (tenant, ref[0])).fetchone() if ref else None
    except psycopg.Error:
        raise
    if prad is None or not isinstance(prad[0], dict):
        conn.rollback()
        return _feilsvar("policy_ukjent", rid)
    policy = prad[0]

    verifikator = konvolutt["verifikator"]
    betrodd_alle = True
    for e in konvolutt["attestasjoner"]:
        trusted = {vid for vid, v in (policy.get("verifikatorer") or {}).items()
                   if isinstance(v, dict)
                   and e["vilkaar"] in (v.get("betrodd_for") or [])}
        if verifikator not in trusted:
            betrodd_alle = False
            break
    if not betrodd_alle:
        # Tilbakekalt eller aldri gitt fullmakt for ett av vilkårene ⇒ HELE
        # kvitteringen avvises. Et delvis betrodd sett er ikke et sett.
        conn.rollback()
        tjeneste.logg.hendelse("kvittering_signatur_ugyldig", rid, tenant,
                               oppdrag_id=oppdrag_id,
                               grunn="autoritet_tilbakekalt_ved_ingest")
        return _feilsvar("kvittering_signatur_ugyldig", rid)

    # v7 pkt. 1: policyen kan sette et TAK på attestasjonslevetid for
    # handlingen. Taket overstyrer verifikatorens eget `utloper` — en
    # verifikator som setter utløp ett år frem kan ikke selv utvide hvor
    # gammelt et faktum tenanten godtar.
    # MÅLHANDLINGEN kommer fra saken, ikke fra policyreferansen:
    # `les_policyref` returnerer (policy_id, VERSJON), ikke handlingen.
    # Med `ref[1]` sto det «1.0.0» der en handlings-id skulle stå, oppslaget
    # traff ingenting, og taket var stille fraværende — altså en kontroll
    # som så ut til å finnes og aldri kunne fyre.
    handling_def = next(
        (h for h in (policy.get("handlinger") or [])
         if isinstance(h, dict) and h.get("id") == policy_ref[1]), {})
    tak = handling_def.get("maks_attestasjon_alder_s")
    tak = tak if isinstance(tak, int) and not isinstance(tak, bool) \
        and tak > 0 else None

    # Scope v2 pkt. 5: `betrodd_for` gir rett til å ATTESTERE et vilkår.
    # Å erklære det prinsipielt uinnhentbart er en annen og større fullmakt
    # — uten den behandles `permanent` som en forbigående negativ, og saken
    # bruker retry-budsjett i stedet for å låses manuelt av en påstand
    # verifikatoren ikke hadde rett til å fremsette.
    kan_permanent = bool(
        ((policy.get("verifikatorer") or {}).get(verifikator) or {})
        .get("kan_fastsla_permanent"))

    # --- 4. Krypter hver attestasjon ------------------------------------
    try:
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    except psycopg.Error:
        raise
    except Exception:
        conn.rollback()
        return _feilsvar("tenantnokkel_mangler", rid)

    naa = datetime.now(timezone.utc)
    resultater = []
    for e in konvolutt["attestasjoner"]:
        att = e.get("attestasjon") or {}
        utstedt = attestering.tid_med_sone(att.get("utstedt")) if att else None
        if att and (utstedt is None
                    or (tak is not None
                        and (naa - utstedt).total_seconds() > tak)):
            conn.rollback()
            tjeneste.logg.hendelse("attestasjon_for_gammel", rid, tenant,
                                   oppdrag_id=oppdrag_id,
                                   vilkaar=e["vilkaar"], tak=tak)
            return _feilsvar("attestasjon_for_gammel", rid)
        ct, nonce = kryptering.krypter(dek, att or {}, tenant, key_id)
        gyldig = attestering.tid_med_sone(att.get("utloper")) if att else naa
        resultater.append({
            "vilkaar": e["vilkaar"], "status": e["status"],
            "permanent": bool(e.get("permanent")) and kan_permanent,
            "attestasjon_kryptert": bytes(ct).hex(),
            "key_id": key_id, "nonce": bytes(nonce).hex(),
            "integritet_hash": hashlib.sha256(bytes(ct)).hexdigest(),
            "gyldig_til": (gyldig or naa).isoformat(),
        })

    # De to SIGNERTE bindingene sendes med — de sammenlignes mot databasen
    # inne i den serialiserte porten, ikke her. Et felt konvolutten krever
    # og verifikatoren signerer, men ingen leser, er dekorasjon.
    utfall = conn.execute(
        "SELECT registrer_verifikasjonsbevis(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (oppdrag_id, oppdragskontrakt.resultathash_verifikasjon(konvolutt),
         konvolutt["krav_sett_hash"], verifikator, konvolutt["nokkel_id"],
         (konvolutt.get("signatur") or {}).get("verdi"),
         json.dumps(resultater), owner_claim, owner_gen,
         konvolutt["verification_generation"],
         konvolutt["fase1_repair_operation_id"])).fetchone()[0]

    if utfall == "avvist":
        # COMMIT, ikke rollback. Porten har skrevet en `verifikasjonskonflikt`
        # -rad om HVILKEN binding som ikke stemte, og den raden er hele
        # poenget: et avvik som ruller bort etterlater ingen evidens for at
        # noen leverte en signert konvolutt med feil generasjon eller feil
        # fase-1-id. Ingen bevis, ingen generasjons- eller saksstatus er
        # rørt på denne veien, og kapabiliteten brennes ikke — den brennes
        # først lenger nede, på den aksepterte veien.
        conn.commit()
        tjeneste.logg.hendelse("kvittering_signatur_ugyldig", rid, tenant,
                               oppdrag_id=oppdrag_id, grunn="db_binding")
        return _feilsvar("kvittering_signatur_ugyldig", rid)
    if utfall == "konflikt":
        conn.commit()
        tjeneste.logg.hendelse("kvittering_konflikt", rid, tenant,
                               oppdrag_id=oppdrag_id, fase="verifikasjon")
        return _feilsvar("kvittering_konflikt", rid)
    if utfall == "idempotent":
        conn.rollback()
        return kanonisk_json({"status": "idempotent", "oppdrag_id": oppdrag_id,
                              "request_id": rid}, 200, {"x-request-id": rid})

    brukt = conn.execute(
        "SELECT bruk_kvitteringskapabilitet(%s,%s)",
        (jti, oppdragskontrakt.resultathash_verifikasjon(konvolutt))
    ).fetchone()[0]
    if brukt not in ("brukt", "idempotent"):
        conn.rollback()
        tjeneste.logg.hendelse("kapabilitet_ugyldig", rid, tenant)
        return _feilsvar("kapabilitet_ugyldig", rid)

    conn.execute(
        "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
        " request_id, detalj) VALUES (%s,%s,%s,%s,%s,%s)",
        (tenant, unntak_id,
         "verifikasjon_positiv" if utfall == "positiv" else "verifikasjon_negativ",
         auth.aktor, rid,
         json.dumps({"oppdrag_id": oppdrag_id, "utfall": utfall,
                     "vilkaar": [e["vilkaar"] for e in resultater],
                     "generation": konvolutt["verification_generation"]},
                    ensure_ascii=False)))
    conn.commit()
    return kanonisk_json({"status": utfall, "oppdrag_id": oppdrag_id,
                          "unntak_id": unntak_id, "request_id": rid},
                         200, {"x-request-id": rid})
