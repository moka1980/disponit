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

import base64
import collections
import hashlib
import hmac
import ipaddress
import json
import math
import os
import queue
import re
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
from . import artefaktskjema
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
#: §4-tallet, pinnet her som i arkivgaten: 25 MiB per dokument. Står i
#: KROPPSGRENSE-blokken og ikke ved handleren fordi transporttakene under
#: REGNES ut av det — to skrivemåter av samme tall er nettopp driften
#: PR-014b-linjen over finnes for å unngå.
_KANDIDAT_DOK_MAKS = 25 * 1024 * 1024
#: Kandidatartefaktets eget budsjett, målt på den KANONISKE JSON-formen i
#: døren: dokumentveiens `_KANDIDAT_DOK_MAKS` for `kildetekst`, og like
#: mye til for ALT det andre kandidaten bærer.
#:
#: «SITATENE ER UTSNITT AV TEKSTEN» VAR IKKE EN GRENSE (Codex P2). Den
#: andre halvdelen var begrunnet med at funnenes sitater per
#: konstruksjon er utsnitt av `kildetekst` og derfor til sammen ikke kan
#: overstige én kopi av den. Utsnitt er de, men de er ikke DISJUNKTE:
#: kontrakten tillater 100 funn, og hvert sitat kan uavhengig dekke
#: hvilken som helst del av teksten. To fulltekstsitater på en 20
#: MiB-kandidat ga ~60 MiB kanonisk JSON, altså `request_feilformet` fra
#: denne porten — og `lagre_kandidat` reiser den som
#: `kandidatlagring_feilet` for HELE den ellers gyldige evalueringen.
#: Antakelsen felte det den skulle beskytte.
#:
#: Grensen bor nå der den kan HÅNDHEVES, i modulkontrakten:
#: `rapportskjema.SITAT_MAKS` = 4096 tegn per sitat, `FUNN_MAKS` = 100
#: funn, håndhevet ved modellgrensen. Verste fall i UTF-8 er 4 byte per
#: tegn, altså 1,6 MiB samlet sitatvolum — mot de 25 MiB denne
#: halvdelen setter av. Intervjuspørsmålene er 20 × 500 tegn (≤ 40 KiB),
#: og resten er avmaskeringskartet og JSON-strukturen. Tallet er som før
#: speilet og ikke importert (api/ importerer aldri modulkode, samme
#: grunn som `_KANDIDAT_ID_KANON`); det som endret seg er at det nå er
#: utledet av tall kontrakten faktisk håndhever.
_KANDIDAT_ARTEFAKT_MAKS = 2 * _KANDIDAT_DOK_MAKS
#: Verste-falls JSON-ekspansjon per KILDEBYTE, samme faktor som
#: PR-014b-linjen over: gyldig JSON kan skrive ett tegn som `\uXXXX`, og
#: en kontrolltegn-byte blir da 6 byte på wire. Kandidatrutene bærer
#: store tekstfelt, så faktoren er ikke teoretisk her — den er forskjellen
#: på et tak som holder og et som avviser gyldige kandidater.
JSON_ESKAPEFAKTOR = 6
#: Claim-trippel, kandidat_id og dokumentnavn. Navnet er ARKIVETS eget
#: (se `_kandidatdata`) og ZIP-formatets navnefelt er 16-bits, altså
#: maks 64 KiB; 1 MiB dekker konvolutten med god margin.
_KANDIDAT_KONVOLUTT = 1024 * 1024
#: #173: dokumentveien inn i kandidatlagrene bærer ETT dokument som
#: base64 (§4-taket 25 MiB → ~33,4 MiB koding) pluss parsetteksten av
#: samme dokument og claim-konvolutten.
#:
#: TEKSTEN ER IKKE «ALDRI STØRRE ENN KILDEN» PÅ WIRE (Codex P2). Den
#: linjen målte parseteksten i DEKODEDE byte, men det som passerer
#: middlewaren er JSON-konvolutten: `\n` blir to byte, et kontrolltegn
#: seks. Et gyldig 25 MiB-uttrekk kunne derfor bli avvist med
#: `body_for_stor` FØR handlerens dokumenterte dekodede sjekk kjørte —
#: og `lagre_dokument` reiser den 4xx-en som `kandidatlagring_feilet`
#: for HELE evalueringen. Taket budsjetterer nå verste fall.
MAKS_KANDIDATDOK_KROPP = (
    # base64: 4 byte per 3 kildebyte, avrundet opp til blokk
    (_KANDIDAT_DOK_MAKS + 2) // 3 * 4
    + JSON_ESKAPEFAKTOR * _KANDIDAT_DOK_MAKS
    + _KANDIDAT_KONVOLUTT)
KANDIDATDOK_RUTE = "/v1/rekruttering/kandidatdokument"
#: #173 (Codex P1): kandidatveien inn i evalueringsartefaktet FALT NED PÅ
#: `MAKS_KROPP` (256 KiB). Kroppen bærer kandidatens hele `kildetekst`
#: pluss funnenes sitater, så enhver kandidat med mer enn en snau side
#: tekst fikk `body_for_stor`; `lagre_kandidat` reiser den som
#: `kandidatlagring_feilet` og feller hele evalueringen. Taket er dørens
#: `_KANDIDAT_ARTEFAKT_MAKS` skrevet i wire-form.
MAKS_KANDIDATARTEFAKT_KROPP = (
    JSON_ESKAPEFAKTOR * _KANDIDAT_ARTEFAKT_MAKS + _KANDIDAT_KONVOLUTT)
KANDIDATARTEFAKT_RUTE = "/v1/rekruttering/kandidatartefakt"
#: M-8 (082): kroppstaket for de uautentiserte tidsvalg-rutene (§3).
MAKS_TIDSVALG_KROPP = 4 * 1024
#: Rutene med eget kroppstak. Oppslag, ikke en voksende kjede av
#: betingede uttrykk: en rute som mangler her får `MAKS_KROPP`, og det
#: er nettopp fallet dette funnet handlet om — da skal det være ÉN
#: leselig linje å se den i.
RUTEKROPPSGRENSER = {
    KANDIDATDOK_RUTE: MAKS_KANDIDATDOK_KROPP,
    KANDIDATARTEFAKT_RUTE: MAKS_KANDIDATARTEFAKT_KROPP,
    # M-8 (082): de offentlige tidsvalg-dørene bærer et token og en
    # slot-id — 4 KiB er romslig, og et mindre tak på en uautentisert
    # rute er billig forsvar i dybden.
    "/v1/tidsvalg/oppslag": MAKS_TIDSVALG_KROPP,
    "/v1/tidsvalg/velg": MAKS_TIDSVALG_KROPP,
}
#: #162: inndata-opplastingen STRØMMES gjennom middlewaren — den teller og
#: videresender chunks, og bufrer aldri. Endepunktet samler derimot opp til
#: dette taket i minnet: v1 krypterer bunten i én operasjon (bevisst
#: v1-grense, dokumentert i 058-headeren; chunket kryptering er egen maskin
#: med eget issue). Taket her er transportens absolutte og styrer
#: middleware-tellingen; reservasjonens deklarerte `maks_bytes` håndhever
#: den KONTRAKTUELLE grensen nedstrøms, og de to måles hver for seg.
INNDATA_MAKS_FYSISK = 64 * 1024 * 1024
STROEM_RUTE_PREFIKS = "/v1/inndata/opplast/"

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
#: #173 (Codex P1): kandidatskrivingen har sin EGEN bøtte, og den er
#: dimensjonert av MODULKONTRAKTENS dokumenterte maksima — ikke av
#: ytelsesporten, som handler om beslutningsflaten.
#:
#: En bunt kan lovlig bære `MAKS_FILER` = 20 000 medlemmer og
#: `antall_soknader` = 5 000 kandidater (kontrakt/KONTRAKT.md, §4), altså
#: 25 000 skrivinger på veien inn i kandidatlagrene. Med standardbudsjettet
#: 12 000/min fikk skriving nummer 12 001 innenfor et rullende minutt 429,
#: og modulens `lever` leser 4xx som TERMINALT: hele evalueringen falt som
#: `kandidatlagring_feilet` fordi plattformen kjørte inn i sin egen grense.
#: Tallene står her og ikke i modulen fordi det er DENNE siden som håndhever
#: dem; api/ importerer aldri modulkode (samme grunn som
#: `_KANDIDAT_ID_KANON` er speilet og ikke importert).
#:
#: FAKTOREN ER HELE RETRYKJEDEN, IKKE HALVE (Codex P2, #173). Her sto
#: faktor 2, med begrunnelsen «modulens retrykjede» — men `lever` gjør
#: inntil `LEVERINGSFORSOK` = 4 forsøk, og faktor 2 budsjetterer bare ETT
#: retry per logisk skriving. Bøtta belastes dessuten av hvert forsøk som
#: NÅR handleren: rate-porten står foran databasearbeidet, så et forsøk
#: som ender i 5xx har allerede tatt sin plass i vinduet.
#:
#: Regnestykket funnet peker på: en kjøring med i snitt to forbigående
#: feil per skriving bruker tre forespørsler per logiske skriving og
#: passerer 50 000 rundt logisk skriving nummer 16 667 — godt under
#: kontraktens 25 000. Neste forsøk får en TERMINAL 429, og `lever` leser
#: 4xx som endelig: en evaluering som var fullt gjenopprettelig ble felt
#: som `kandidatlagring_feilet` av plattformens egen grense. Nøyaktig
#: klassen bøtta ble laget for å fjerne, bare flyttet lenger ut i bunten.
#:
#: Budsjettet dekker derfor ALLE forsøkene. Den andre utveien — å ikke
#: belaste retryer som ferske logiske skrivinger — krever at bøtta kan
#: kjenne igjen et gjentatt skriv, altså idempotensnøkler inn i
#: rate-laget: ny maskin, og ikke en fiksrunde-endring (§9 K1).
#:
#: `LEVERINGSFORSOK` SPEILES, den importeres ikke: api/ importerer aldri
#: modulkode (samme grunn som `_KANDIDAT_ID_KANON` er speilet). Speilet
#: er bundet til modulens eget tall av
#: `test_173_ratebudsjettet_dekker_hele_retrykjeden`, så de to kan ikke
#: drive fra hverandre i stillhet.
#:
#: PRISEN ER BETALT I BØTTEFORMEN, IKKE I TAKET (Codex P2). Linjen her sa
#: at `slipp_gjennom` bygger vindulisten på nytt per kall — ~312
#: millioner float-sammenligninger for en full bunt — og godtok det som
#: «ikke veiens toppunkt». Det stemte per skriving, men kostnaden lå
#: under den DELTE låsen: hver annen rate-sjekk i prosessen ventet på et
#: arbeid bare denne bøtta hadde bruk for. Bøtta er nå en `deque` som
#: forlater hvert tidspunkt én gang (amortisert O(1)), så taket kan være
#: kontraktens tall uten å være et ytelsesspørsmål.
KANDIDAT_LEVERINGSFORSOK = 4          # speiler m57 controller.LEVERINGSFORSOK
KANDIDATDATA_RATE_PER_MIN = \
    KANDIDAT_LEVERINGSFORSOK * (20_000 + 5_000)           # 100 000/min
SIDE_STANDARD, SIDE_MAKS = 50, 200
#: Statusene der saksbehandlingen ER FERDIG. Alt annet i statusmaskinen
#: (migrasjon 011) venter på et menneske eller en maskin, og er dermed «åpen».
#: Denne veien rundt — terminal er listet opp, åpen er «resten» — er ikke
#: smakssak: en tillatelsesliste over åpne statuser MÅ vedlikeholdes hver gang
#: statusmaskinen vokser, og gjør den ikke det, forsvinner saker som venter på
#: en godkjenner stille ut av køen. Nøyaktig det skjedde med dashbordets
#: `AAPNE`-liste, som manglet alle fire godkjenningsstatusene fra PR-012.
#: Blir det noen gang en tredje terminal status, står den her — og ingen andre
#: steder.
TERMINALE_UNNTAKSSTATUSER = ("løst", "avvist")

#: Sakstypene i `unntak` (migrasjon 003). `sikkerhet` og `drift` er EGNE
#: køer med eget scope (v3-delta pkt. 5), og vernet gjelder ikke bare
#: saksinnholdet: at det FINNES en sikkerhetssak er selv den beskyttede
#: opplysningen — derfor svarer `_hent_unntak` `ikke_funnet` og ikke 403.
SAKSTYPER = ("normal", "sikkerhet", "drift")


def synlige_sakstyper(scopes) -> tuple[str, ...]:
    """Sakstypene dette tokenet får se — DEN ENE avledningen av regelen.

    Regelen bodde tidligere som en naken `!= "normal"`-test inne i
    unntakslisten, altså i ETT endepunkt og ikke i domenet. Det gjorde
    den umulig å arve: M-16-nøkkeltallene leser de samme radene, og
    fordi de leser dem via egne definere kom de utenom testen — kategori-
    og tilstandstellinger, og IDer, tidspunkter og sakstyper for lukkede
    saker fra sikkerhets- og driftskøene, lå dermed åpne for ethvert
    `decisions:read`. En scope-regel som bare finnes i én leser er en
    regel det neste endepunktet ikke vet om; her er den én funksjon, og
    hver leser av `unntak` spør den.
    """
    if "security:read" in scopes:
        return SAKSTYPER
    return ("normal",)


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

    #: Taket på antall nøkler som holdes samtidig (Codex P1). Nøkkelen er
    #: ikke alltid en identitet SERVEREN har utstedt: den uautentiserte
    #: innløsningsruten nøkler på onboarding-id-en fra kroppen, altså på
    #: KLIENTINPUT. En dict som bare vokser er da en gratis minneflate —
    #: hver ferske id la igjen en liste ingen noensinne ser på igjen, siden
    #: opprydding bare skjer når nøyaktig samme nøkkel kommer tilbake.
    #: Over taket feies alle nøkler uten treff i vinduet; de er per
    #: definisjon uten betydning for en grense som bare ser 60 sekunder
    #: bakover.
    NOKKELTAK = 4096

    def __init__(self, per_minutt: int) -> None:
        self.per_minutt = per_minutt
        self._treff: dict[str, collections.deque[float]] = {}
        self._laas = threading.Lock()

    def slipp_gjennom(self, nokkel: str, naa: float | None = None, *,
                      tak: int | None = None) -> bool:
        """`tak` overstyrer budsjettet for NØYAKTIG denne nøkkelen.

        Ruter som trenger et eget, strammere budsjett enn prosessens
        standard (12 000/min, satt av ytelsesporten) sender det inn her i
        stedet for å holde sin egen grense — én bøtteimplementasjon, ett
        sted å lese om vinduet.
        """
        grense = tak if tak is not None else self.per_minutt
        with self._laas:
            # TIDSPUNKTET TAS UNDER LÅSEN (Codex P2). Det ble tidligere
            # samplet FØR `with self._laas`, og da er det ingen
            # sammenheng mellom rekkefølgen tidspunktene får og
            # rekkefølgen trådene faktisk skriver i: en tråd som blir
            # deschedulert mellom de to linjene vekker opp og appender et
            # GAMMELT tidspunkt bak nyere innslag.
            #
            # Køen tåler ikke den inversjonen, og det er ikke en
            # skjønnhetsfeil — begge endepunktene den leses fra antar
            # sortert innhold. `popleft`-løkken under stopper på det
            # første innslaget som ikke er utløpt, så et gammelt
            # tidspunkt bak et nyere blir ALDRI forlatt: bøtta bærer et
            # utløpt treff for alltid og avviser lovlig trafikk. Og
            # nøkkelfeiingen over måler `v[-1]` som «nyeste», så en
            # inversjon der kan slette en bøtte som fortsatt har
            # levende treff — motsatt feil, samme årsak.
            #
            # Ingen produksjonskaller sender `naa`; parameteren er
            # testsømmen, og en test som oppgir sine egne tidspunkter
            # eier rekkefølgen selv.
            if naa is None:
                naa = time.monotonic()
            vindustart = naa - 60.0
            if len(self._treff) > self.NOKKELTAK:
                self._treff = {k: v for k, v in self._treff.items()
                               if v and v[-1] > vindustart}
            tider = self._treff.get(nokkel)
            if tider is None:
                tider = self._treff[nokkel] = collections.deque()
            # HVERT TIDSPUNKT UTLØPER ÉN GANG (Codex P2). Linjen her
            # bygde vindulisten på nytt per kall — O(vindu) — og gjorde
            # det mens den DELTE låsen sto. Med kandidatbøttas vindu på
            # 50 000 kostet én full bunt (25 000 skriv, kontraktens
            # maksimum) ~312 millioner sammenligninger, og hver av dem
            # holdt låsen hver annen rate-sjekk i prosessen venter på.
            # Prisen var beskrevet i konstantens kommentar og godtatt som
            # «ikke veiens toppunkt» — men den er betalt av ALLE ruter,
            # ikke bare av den som betalte for den.
            #
            # Køen gir samme vindu til amortisert O(1): tidspunktene står
            # sortert fordi de settes inn i kalltidsrekkefølge (samme
            # antakelse som feiingen over alt bygger på, `v[-1]`), så det
            # som har falt ut av vinduet ligger fremst og forlates én
            # gang — ikke én gang per etterfølgende kall.
            while tider and tider[0] <= vindustart:
                tider.popleft()
            if len(tider) >= grense:
                return False
            tider.append(naa)
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

        # #162: inndata-strømmen bufres ALDRI i middlewaren — chunks telles
        # og videresendes; taket avbryter midt i strømmen, aldri etter den.
        if scope.get("path", "").startswith(STROEM_RUTE_PREFIKS):
            return await self._stroem(scope, receive, send, rid, headere)
        # PR-014b §7: opplastingsruten tåler en større kropp (1 MiB-artefakt).
        maks = (MAKS_ARTEFAKT_KROPP if scope.get("path") in STORE_KROPP_RUTER
                else RUTEKROPPSGRENSER.get(scope.get("path"), self.maks))
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

        # ÉN KROPP, IKKE TRE (Codex P1, #173). Linjene under lagde
        # `bytes(kropp)` TO ganger — én til `scope["state"]`, én til
        # replay — mens `kropp` selv holdt bytearray-bufferet levende
        # gjennom hele `await self.app`. Det er tre samtidige kopier av
        # en kropp som på kandidatartefaktruten kan være ~301 MiB, og
        # middlewaren kjører FØR enhver autentisering: en forespørsel med
        # ugyldig token betalte samme minne som en gyldig.
        #
        # Kopien tas nå én gang, deles av begge lesere, og bytearray-en
        # slippes før handleren kalles. `bytearray.clear()` frigjør
        # FAKTISK bufferet — CPythons `PyByteArray_Resize` har egen arm
        # for størrelse 0 som kaller `PyObject_Free` — så dette er en
        # frigjøring, ikke bare en dereferanse som venter på GC.
        data = bytes(kropp)
        kropp.clear()
        ferdig = False

        async def replay():
            nonlocal ferdig
            if ferdig:
                return {"type": "http.disconnect"}
            ferdig = True
            return {"type": "http.request", "body": data,
                    "more_body": False}

        scope["state"]["kropp"] = data
        return await self.app(scope, replay, send)

    async def _stroem(self, scope, receive, send, rid, headere):
        """Tellende gjennomstrømming for inndata-ruten (#162): endepunktet
        leser `request.stream()` selv; her håndheves KUN det absolutte
        transporttaket, byte for byte, uten å samle kroppen."""
        # Den ÅPENBART for store avvises uten å lese en eneste byte, som
        # for alle andre ruter over (Cursor P2-5). Uten dette ville
        # `Content-Length: 10**9` tvunget hele veien opp til 64 MiB-kuttet
        # før klienten fikk et 413 den kunne fått med det samme. Samme
        # `isdigit`-form som hovedveien: en Content-Length som ikke er et
        # tall er en ugyldig forespørsel, ikke noe å gjette på.
        oppgitt = headere.get("content-length")
        if oppgitt is not None:
            if not oppgitt.isdigit():
                return await self._avvis(send, "body_lengde_ugyldig", rid)
            if int(oppgitt) > INNDATA_MAKS_FYSISK:
                return await self._avvis(send, "body_for_stor", rid)
        talt = 0
        avvist = False

        async def tellende():
            nonlocal talt, avvist
            melding = await receive()
            if melding["type"] == "http.request":
                talt += len(melding.get("body", b""))
                if talt > INNDATA_MAKS_FYSISK:
                    avvist = True
                    # Endepunktet ser strømmen slutte og leser `avvist`
                    # via scope-state — det svarer med den kodede feilen.
                    scope["state"]["inndata_for_stor"] = True
                    return {"type": "http.request", "body": b"",
                            "more_body": False}
            return melding

        return await self.app(scope, tellende, send)

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


class ModulAutentisert(Autentisert):
    """En autentisert MODULDEPLOYMENT (035): tokenet svarer på nøyaktig ett
    spørsmål — hvilken deployment er dette? Alt annet (livsløp, status,
    epoch-gyldighet, oppdrags-/artefakttyper) slås opp ved HVER bruk via
    releasens kontrakt; scopes lagres aldri og utledes aldri her.

    `tenant` er modul-id-en (kun logg/rate — modultokener er tenantløse;
    forretningstenanten kommer alltid fra det claimede oppdraget), `rolle`
    er modul-id-en (samme konvensjon som modulens api-tokener: claim-SQL-en
    bruker rollen som eiermodul)."""
    __slots__ = ("modul_id", "miljo", "release_id", "utstedt_epoch",
                 "modultoken_id")

    def __init__(self, modul_id, miljo, release_id, utstedt_epoch,
                 modultoken_id):
        super().__init__(modul_id, modul_id, (), f"mtk_{modultoken_id}")
        self.modul_id, self.miljo = modul_id, miljo
        self.release_id, self.utstedt_epoch = release_id, utstedt_epoch
        self.modultoken_id = modultoken_id


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
        if raa.startswith("mtk_"):
            # Modultoken (035): `mtk_<token_id>.<secret>`. Oppslaget går via
            # den herdede `verifiser_modultoken` (runtime har hverken SELECT
            # eller skriving på modultoken) og er gyldighets-filtrert der:
            # tilbakekalt-og-forbi eller utløpt → ingen rad. Token-id-en i
            # wire-formatet må MATCHE radens (ellers kunne en gjettet id
            # pares med en stjålet MAC fra et annet token).
            tid_del, _, msecret = raa[4:].partition(".")
            if not tid_del or not msecret:
                return None
            mrad = conn.execute(
                "SELECT token_id, modul_id, miljo, release_id, utstedt_epoch"
                "  FROM verifiser_modultoken(%s)",
                (_mac(tjeneste.pepper, msecret),)).fetchone()
            if mrad is None or str(mrad[0]) != tid_del:
                return None
            return ModulAutentisert(mrad[1], mrad[2], mrad[3], mrad[4],
                                    mrad[0])
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


def _modultoken_revalidert(tjeneste: Tjeneste, conn: psycopg.Connection,
                           auth: Autentisert, rid: str) -> Response | None:
    """Er deploymenten FORTSATT autorisert, her og nå? -> None = ja.

    Codex P1. `preauth` eier og LUKKER sin egen transaksjon, og
    forretningstransaksjonen starter etterpå. Mellom de to committene kan
    `noddeaktiver_modul` ha kjørt — det nødstoppet som er annonsert å
    terminere tokenfamilien ØYEBLIKKELIG. Men `ModulAutentisert`-objektet
    lever videre over transaksjonsgrensen: et token nødstoppet faktisk
    drepte, er fortsatt representert som en autentisert deployment i
    requesten som alt er i gang. Kapabilitetsinnløsningene sammenligner kun
    IDENTITET (modul, miljø, release) og leser hverken tokenets tilstand,
    modulens status eller epoch — så kvitteringen eller artefaktet fra den
    stoppede deploymenten gikk inn likevel, og stoppet var et løfte
    plattformen ikke holdt.

    ALLE TRE VEIENE BRUKER DEN (Codex P1). Først de to innløsningsveiene;
    siden også claim-porten. Claim-veien så lenge ut til å være dekket av at
    `claim_neste_oppdrag` re-verifiserer under modul-låsen, men den
    re-verifiseringen gjelder REGISTERET — deployment, status, epoch.
    Funksjonen får ingen token-id og kan derfor ikke se `tilbakekalt_ts`, så
    en eksplisitt tilbakekalling midt i requesten stanset ingenting: det
    tilbakekalte tokenet ble tildelt nytt arbeid. Porten står FØR bruken på
    alle tre stedene, og låsen den tar er transaksjonsbundet — et nødstopp
    eller en tilbakekalling kan ikke gli inn mellom dommen og forbruket.

    ÉN funksjon for begge veiene, og dommen selv ligger i databasen
    (`modultoken_fortsatt_autorisert`) — den er ikke sammensatt av tre
    oppslag herfra som et nødstopp kunne kilt seg inn mellom.

    Et legacy-api-token har ingen deployment å revalidere; scope-porten er
    hele dets autorisasjon, og den er alt passert.
    """
    if not isinstance(auth, ModulAutentisert):
        return None
    utfall = conn.execute(
        "SELECT modultoken_fortsatt_autorisert(%s,%s,%s,%s,%s)",
        (auth.modultoken_id, auth.modul_id, auth.miljo, auth.release_id,
         auth.utstedt_epoch)).fetchone()[0]
    if utfall == "ok":
        return None
    conn.rollback()
    tjeneste.logg.hendelse(utfall, rid, auth.tenant, modul=auth.modul_id,
                           miljo=auth.miljo, release=auth.release_id,
                           utstedt=auth.utstedt_epoch)
    return _feilsvar(utfall, rid)


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

    def oppdrag_forny(request: Request) -> Response:
        return _oppdrag_forny(tjeneste, request)

    def kandidatdokument(request: Request) -> Response:
        return _kandidatdata(tjeneste, request, "dokument")

    def kandidatartefakt(request: Request) -> Response:
        return _kandidatdata(tjeneste, request, "kandidat")

    def artefakt_upload(request: Request) -> Response:
        return _artefakt_upload(tjeneste, request)

    # PR-008: lese-endepunktene. Importen ligger her — ETTER at modulens
    # hjelpere er definert — fordi `lesing` importerer dem på modulnivå.
    from . import lesing

    def oversikt(request: Request) -> Response:
        return lesing.oversikt(tjeneste, request)

    def nokkeltall(request: Request) -> Response:
        return lesing.nokkeltall(tjeneste, request)

    def beslutninger(request: Request) -> Response:
        return lesing.beslutninger(tjeneste, request)

    def beslutning_detalj(request: Request) -> Response:
        return lesing.beslutning_detalj(tjeneste, request)

    def rapport_detalj(request: Request) -> Response:
        return lesing.rapport_detalj(tjeneste, request)

    def rekrutteringsrapport_detalj(request: Request) -> Response:
        return lesing.rekrutteringsrapport_detalj(tjeneste, request)

    def rekrutteringsevalueringer(request: Request) -> Response:
        return lesing.rekrutteringsevalueringer(tjeneste, request)

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

    def policy_aktive(request: Request) -> Response:
        return lesing.policy_aktive(tjeneste, request)

    def utrulling(request: Request) -> Response:
        return lesing.utrulling(tjeneste, request)

    def modellstyring(request: Request) -> Response:
        return lesing.modellstyring(tjeneste, request)

    # 089 (M-35): kontinuitetsflaten. Leseveien er tenantens egen
    # beredskapstilstand; de tre skriveveiene gjør hver nøyaktig ett
    # kall mot en claimer-eid dør i 089 — etteranalyse-kravet og
    # append-only bor DER, aldri her.
    def kontinuitet(request: Request) -> Response:
        from . import kontinuitet as kontinuitetsmodul
        return kontinuitetsmodul.kontinuitet(tjeneste, request)

    def kontinuitet_hendelser(request: Request) -> Response:
        from . import kontinuitet as kontinuitetsmodul
        return kontinuitetsmodul.hendelser_endepunkt(tjeneste, request)

    def kontinuitet_post(request: Request) -> Response:
        from . import kontinuitet as kontinuitetsmodul
        return kontinuitetsmodul.post_endepunkt(tjeneste, request)

    def kontinuitet_lukk(request: Request) -> Response:
        from . import kontinuitet as kontinuitetsmodul
        return kontinuitetsmodul.lukk_endepunkt(tjeneste, request)

    # 094 (M-5): malregisteret. Leseveien er hele registeret i én
    # transaksjon; de fire skrivende POST-ene gjør hver nøyaktig ett kall
    # mot en mal_eier-eid dør i 094. Append-only, låste klausuler og
    # felt-totaliteten bor DER, aldri her.
    def dokumentmal(request: Request) -> Response:
        from . import dokumentmal as dokumentmalmodul
        return dokumentmalmodul.dokumentmal(tjeneste, request)

    def dokumentmal_familier(request: Request) -> Response:
        from . import dokumentmal as dokumentmalmodul
        return dokumentmalmodul.familie_endepunkt(tjeneste, request)

    def dokumentmal_versjoner(request: Request) -> Response:
        from . import dokumentmal as dokumentmalmodul
        return dokumentmalmodul.versjon_endepunkt(tjeneste, request)

    def dokumentmal_publiser(request: Request) -> Response:
        from . import dokumentmal as dokumentmalmodul
        return dokumentmalmodul.publiser_endepunkt(tjeneste, request)

    def dokumentmal_trekk_tilbake(request: Request) -> Response:
        from . import dokumentmal as dokumentmalmodul
        return dokumentmalmodul.trekk_tilbake_endepunkt(tjeneste, request)

    # Utfyllingen RETURNERER. Den lagrer ikke, sender ikke, publiserer
    # ikke — og `m5_fyll_mal` er STABLE, så basen håndhever det.
    def dokumentmal_utfylling(request: Request) -> Response:
        from . import dokumentmal as dokumentmalmodul
        return dokumentmalmodul.utfylling_endepunkt(tjeneste, request)
    # 096 (M-21): pliktregisteret. Leseveien er tenantens eget register;
    # de tre skriveveiene er menneskelige handlinger i flaten, bak
    # `bestilling:opprett` + CSRF + Idempotency-Key. Sveipen som køer
    # fristvarslene finnes IKKE som HTTP — den er et forpass i
    # varselsenderen, med sitt eget grant til `disponit_varselsender`.
    def plikt_liste(request: Request) -> Response:
        from . import plikt as pliktmodul
        return pliktmodul.plikter(tjeneste, request)

    def plikt_registrer(request: Request) -> Response:
        from . import plikt as pliktmodul
        return pliktmodul.registrer_endepunkt(tjeneste, request)

    def plikt_lukk(request: Request) -> Response:
        from . import plikt as pliktmodul
        return pliktmodul.lukk_endepunkt(tjeneste, request)

    def plikt_bortfall(request: Request) -> Response:
        from . import plikt as pliktmodul
        return pliktmodul.bortfall_endepunkt(tjeneste, request)
    # 100 (M-34): kontrollregisteret. Leseveien er tenantens eget
    # register; de tre skriveveiene er menneskelige handlinger i flaten,
    # bak `bestilling:opprett` + CSRF + Idempotency-Key.
    # Etterprøvingssveipen finnes IKKE som HTTP — den har sin egen
    # LOGIN-rolle (`disponit_compliancesveip`) og sin egen daglige timer,
    # og runtime er eksplisitt REVOKEt fra den i 100.
    #
    # OG DET FINNES INGEN INNSENDINGSVEI. Katalogteksten lover innsending
    # til sertifiseringsorgan; v1 registrerer kontrollen. Fraværet er
    # dommen, ikke en manglende rute.
    def compliance_bilde(request: Request) -> Response:
        from . import compliance as compliancemodul
        return compliancemodul.kontrollbilde(tjeneste, request)

    def compliance_registrer(request: Request) -> Response:
        from . import compliance as compliancemodul
        return compliancemodul.registrer_endepunkt(tjeneste, request)

    def compliance_etterproving(request: Request) -> Response:
        from . import compliance as compliancemodul
        return compliancemodul.etterproving_endepunkt(tjeneste, request)

    def compliance_ikke_relevant(request: Request) -> Response:
        from . import compliance as compliancemodul
        return compliancemodul.ikke_relevant_endepunkt(tjeneste, request)

    # 101 (M-13): avstemmingsregisteret. Leseveien er tenantens eget
    # bank- og bilagsregister; de fem skriveveiene er menneskelige
    # handlinger i flaten, bak `bestilling:opprett` + CSRF +
    # Idempotency-Key. Avstemmingssveipen finnes IKKE som HTTP — den har
    # sin egen LOGIN-rolle (`disponit_avstemmingssveip`) og sin egen
    # daglige timer, og runtime er eksplisitt REVOKEt fra den i 101.
    #
    # OG DET FINNES INGEN BOKFØRINGSVEI. Katalogteksten lover automatisk
    # bokføring ved full match; v1 avstemmer og viser. Fraværet er
    # dommen, ikke en manglende rute.
    def avstemming_bilde(request: Request) -> Response:
        from . import avstemming as avstemmingmodul
        return avstemmingmodul.avstemmingsbilde(tjeneste, request)

    def avstemming_konto(request: Request) -> Response:
        from . import avstemming as avstemmingmodul
        return avstemmingmodul.konto_endepunkt(tjeneste, request)

    def avstemming_bankpost(request: Request) -> Response:
        from . import avstemming as avstemmingmodul
        return avstemmingmodul.bankpost_endepunkt(tjeneste, request)

    def avstemming_bilag(request: Request) -> Response:
        from . import avstemming as avstemmingmodul
        return avstemmingmodul.bilag_endepunkt(tjeneste, request)

    def avstemming_match(request: Request) -> Response:
        from . import avstemming as avstemmingmodul
        return avstemmingmodul.match_endepunkt(tjeneste, request)

    def avstemming_opphev(request: Request) -> Response:
        from . import avstemming as avstemmingmodul
        return avstemmingmodul.opphev_endepunkt(tjeneste, request)

    # 102 (M-17): henvendelsesregisteret. To leseveier for KØEN og
    # INNHOLDET (ulike scopes, fordi bare det ene er persondata) og seks
    # skriveveier — alle menneskelige handlinger i flaten, bak
    # `bestilling:opprett` + CSRF + Idempotency-Key.
    #
    # OG DET FINNES INGEN SENDEVEI. Katalogteksten lover automatiske
    # svar; v1 lagrer et utkast. Fraværet er dommen, ikke en manglende
    # rute — og `m17_avgjor_utkast` har ingen status som heter `sendt`.
    def kundeservice_koe(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.kobilde(tjeneste, request)

    def kundeservice_innhold(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.innhold_endepunkt(tjeneste, request)

    def kundeservice_utkastene(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.utkastene_endepunkt(tjeneste, request)

    def kundeservice_ta_imot(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.ta_imot_endepunkt(tjeneste, request)

    def kundeservice_klassifiser(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.klassifiser_endepunkt(tjeneste, request)

    def kundeservice_unntakskoe(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.unntakskoe_endepunkt(tjeneste, request)

    def kundeservice_utkast(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.utkast_endepunkt(tjeneste, request)

    def kundeservice_utkastdom(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.utkastdom_endepunkt(tjeneste, request)

    def kundeservice_lukk(request: Request) -> Response:
        from . import kundeservice as ksmodul
        return ksmodul.lukk_endepunkt(tjeneste, request)

    # 103 (M-18): onboardingregisteret. To leseveier (løpene og ett løps
    # steg) og seks skriveveier — alle menneskelige handlinger i flaten,
    # bak `bestilling:opprett` + CSRF + Idempotency-Key.
    #
    # OG DET FINNES INGEN PROVISJONERINGSVEI. Katalogteksten lover 0
    # minutter per ny kunde; v1 registrerer løpet. Fraværet er dommen,
    # ikke en manglende rute.
    def onboarding_bilde(request: Request) -> Response:
        from . import onboarding as obmodul
        return obmodul.onboardingbilde(tjeneste, request)

    def onboarding_stegene(request: Request) -> Response:
        from . import onboarding as obmodul
        return obmodul.stegene_endepunkt(tjeneste, request)

    def onboarding_mal(request: Request) -> Response:
        from . import onboarding as obmodul
        return obmodul.mal_endepunkt(tjeneste, request)

    def onboarding_malsteg(request: Request) -> Response:
        from . import onboarding as obmodul
        return obmodul.malsteg_endepunkt(tjeneste, request)

    def onboarding_start(request: Request) -> Response:
        from . import onboarding as obmodul
        return obmodul.start_endepunkt(tjeneste, request)

    def onboarding_stegeier(request: Request) -> Response:
        from . import onboarding as obmodul
        return obmodul.stegeier_endepunkt(tjeneste, request)

    def onboarding_fullfor(request: Request) -> Response:
        from . import onboarding as obmodul
        return obmodul.fullfor_endepunkt(tjeneste, request)

    def onboarding_avslutt(request: Request) -> Response:
        from . import onboarding as obmodul
        return obmodul.avslutt_endepunkt(tjeneste, request)

    # 104 (M-23): fordringsregisteret. To leseveier og fem skriveveier —
    # alle menneskelige handlinger i flaten.
    #
    # OG DET FINNES INGEN SENDEVEI. Katalogteksten lover et forslag om
    # nedbetalingsplan til kunden; v1 registrerer fordringen. Fraværet er
    # dommen, og den er den strengeste i klyngen: en purring til feil
    # kunde kan ikke trekkes tilbake.
    def fordring_bilde(request: Request) -> Response:
        from . import fordring as fordringmodul
        return fordringmodul.fordringsbilde(tjeneste, request)

    def fordring_hendelser(request: Request) -> Response:
        from . import fordring as fordringmodul
        return fordringmodul.hendelsene_endepunkt(tjeneste, request)

    def fordring_purreplan(request: Request) -> Response:
        from . import fordring as fordringmodul
        return fordringmodul.purreplan_endepunkt(tjeneste, request)

    def fordring_registrer(request: Request) -> Response:
        from . import fordring as fordringmodul
        return fordringmodul.registrer_endepunkt(tjeneste, request)

    def fordring_betaling(request: Request) -> Response:
        from . import fordring as fordringmodul
        return fordringmodul.betaling_endepunkt(tjeneste, request)

    def fordring_neste_trinn(request: Request) -> Response:
        from . import fordring as fordringmodul
        return fordringmodul.neste_trinn_endepunkt(tjeneste, request)

    def fordring_ettergi(request: Request) -> Response:
        from . import fordring as fordringmodul
        return fordringmodul.ettergi_endepunkt(tjeneste, request)

    # 105 (M-24): leverandør- og SLA-registeret. To leseveier og fem
    # skriveveier — alle menneskelige handlinger i flaten.
    #
    # OG DET FINNES INGEN BETALINGSVEI. Katalogteksten lover
    # leverandørbetaling innen policygrenser; v1 registrerer avtalen og
    # måler leveransen mot den. Fraværet er dommen: en utgående betaling
    # er den ene handlingen i katalogen som er umulig å angre — pengene
    # er borte, og de er borte hos noen andre.
    #
    # OG INGEN PRISVEI: katalogen deler marginbeskyttelsen, M-24
    # oppdager og M-26 foreslår. Ingen av rutene under setter en pris.
    def leverandor_bilde(request: Request) -> Response:
        from . import leverandor as leverandormodul
        return leverandormodul.leverandorbilde(tjeneste, request)

    def leverandor_leveranser(request: Request) -> Response:
        from . import leverandor as leverandormodul
        return leverandormodul.leveransene_endepunkt(tjeneste, request)

    def leverandor_terskler(request: Request) -> Response:
        from . import leverandor as leverandormodul
        return leverandormodul.terskler_endepunkt(tjeneste, request)

    def leverandor_part(request: Request) -> Response:
        from . import leverandor as leverandormodul
        return leverandormodul.registrer_part_endepunkt(tjeneste, request)

    def leverandor_avtale(request: Request) -> Response:
        from . import leverandor as leverandormodul
        return leverandormodul.registrer_avtale_endepunkt(tjeneste,
                                                          request)

    def leverandor_leveranse(request: Request) -> Response:
        from . import leverandor as leverandormodul
        return leverandormodul.registrer_leveranse_endepunkt(tjeneste,
                                                             request)

    def leverandor_avslutt(request: Request) -> Response:
        from . import leverandor as leverandormodul
        return leverandormodul.avslutt_avtale_endepunkt(tjeneste, request)

    # 106 (M-14): fakturakontrollen. To leseveier og fem skriveveier.
    #
    # OG DET FINNES INGEN BOKFØRINGSVEI OG INGEN ATTESTASJONSVEI.
    # Policyen vi sender ut navngir modulen som `v_regnskap`, betrodd
    # for `faktura_godkjent` — og bruker den attestasjonen til å la
    # `faktura.bokfor` gå automatisk. v1 registrerer hva kontrollene SÅ;
    # den signerer ingenting, og ingen av rutene under er en bokføring.
    def faktura_bilde(request: Request) -> Response:
        from . import faktura as fakturamodul
        return fakturamodul.fakturabilde(tjeneste, request)

    def faktura_kontroller(request: Request) -> Response:
        from . import faktura as fakturamodul
        return fakturamodul.kontrollene_endepunkt(tjeneste, request)

    def faktura_terskler(request: Request) -> Response:
        from . import faktura as fakturamodul
        return fakturamodul.terskler_endepunkt(tjeneste, request)

    def faktura_mvasats(request: Request) -> Response:
        from . import faktura as fakturamodul
        return fakturamodul.mvasats_endepunkt(tjeneste, request)

    def faktura_registrer(request: Request) -> Response:
        from . import faktura as fakturamodul
        return fakturamodul.registrer_endepunkt(tjeneste, request)

    def faktura_kontroll(request: Request) -> Response:
        from . import faktura as fakturamodul
        return fakturamodul.kontroll_endepunkt(tjeneste, request)

    def faktura_avgjor(request: Request) -> Response:
        from . import faktura as fakturamodul
        return fakturamodul.avgjor_endepunkt(tjeneste, request)
    # 107 (M-25): prosjekt- og kontraktregisteret. Tre leseveier og
    # seks skriveveier.
    #
    # OG DET FINNES INGEN FAKTURAVEI OG INGEN ATTESTASJONSVEI. Policyen
    # vi sender ut navngir modulen som `v_prosjekt`, betrodd for
    # `milepael_dokumentert`, og bruker den attestasjonen til å la
    # `ordre.bekreft_og_fakturer` gå automatisk. v1 registrerer at en
    # milepæl er nådd OG hva som dokumenterer den; den stiller ingen
    # krav og signerer ingenting.
    def prosjekt_bilde(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.prosjektbilde(tjeneste, request)

    def prosjekt_milepaeler(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.milepaelene_endepunkt(tjeneste, request)

    def prosjekt_arbeidsliste(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.arbeidet_endepunkt(tjeneste, request)

    def prosjekt_terskler(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.terskler_endepunkt(tjeneste, request)

    def prosjekt_registrer(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.registrer_endepunkt(tjeneste, request)

    def prosjekt_betalingsplan(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.betalingsplan_endepunkt(tjeneste, request)

    def prosjekt_milepael(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.naa_milepael_endepunkt(tjeneste, request)

    def prosjekt_arbeid(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.registrer_arbeid_endepunkt(tjeneste, request)

    def prosjekt_avslutt(request: Request) -> Response:
        from . import prosjekt as prosjektmodul
        return prosjektmodul.avslutt_endepunkt(tjeneste, request)

    # 108 (M-26): prisboka. Tre leseveier og fem skriveveier.
    #
    # OG DET FINNES INGEN TILBUDSVEI. Alle tre bransjemalene navngir
    # modulen som `v_prisbok` og bruker `priser_fra_prisbok` til å la
    # `tilbud.generer` gå automatisk. v1 er boka; et tilbud er et
    # bindende utspill mot en kunde, og det lages ikke her.
    def prisbok_bilde(request: Request) -> Response:
        from . import prisbok as prisbokmodul
        return prisbokmodul.prisbokbilde(tjeneste, request)

    def prisbok_historikk(request: Request) -> Response:
        from . import prisbok as prisbokmodul
        return prisbokmodul.historikk_endepunkt(tjeneste, request)

    def prisbok_paa_dato(request: Request) -> Response:
        from . import prisbok as prisbokmodul
        return prisbokmodul.paa_dato_endepunkt(tjeneste, request)

    def prisbok_terskler(request: Request) -> Response:
        from . import prisbok as prisbokmodul
        return prisbokmodul.terskler_endepunkt(tjeneste, request)

    def prisbok_produkt(request: Request) -> Response:
        from . import prisbok as prisbokmodul
        return prisbokmodul.registrer_produkt_endepunkt(tjeneste, request)

    def prisbok_pris(request: Request) -> Response:
        from . import prisbok as prisbokmodul
        return prisbokmodul.sett_pris_endepunkt(tjeneste, request)

    def prisbok_klausul(request: Request) -> Response:
        from . import prisbok as prisbokmodul
        return prisbokmodul.sett_klausul_endepunkt(tjeneste, request)

    def prisbok_aktiv(request: Request) -> Response:
        from . import prisbok as prisbokmodul
        return prisbokmodul.sett_aktiv_endepunkt(tjeneste, request)

    # 109 (M-27): lagerregisteret. Tre leseveier og seks skriveveier.
    #
    # OG DET FINNES INGEN BESTILLINGSVEI. To av tre bransjemaler navngir
    # modulen som `v_lager` og bruker `lager_reservert` til å la
    # `lager.bestill_pafyll` gå automatisk. v1 skriver FUNNET; en
    # bestilling binder virksomheten økonomisk.
    def lager_bilde(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.lagerbilde(tjeneste, request)

    def lager_bevegelser(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.bevegelser_endepunkt(tjeneste, request)

    def lager_paa_dato(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.paa_dato_endepunkt(tjeneste, request)

    def lager_terskler(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.terskler_endepunkt(tjeneste, request)

    def lager_vare(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.registrer_vare_endepunkt(tjeneste, request)

    def lager_punkt(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.sett_punkt_endepunkt(tjeneste, request)

    def lager_bevegelse(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.registrer_bevegelse_endepunkt(tjeneste, request)

    def lager_telling(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.registrer_telling_endepunkt(tjeneste, request)

    def lager_aktiv(request: Request) -> Response:
        from . import lager as lagermodul
        return lagermodul.sett_aktiv_endepunkt(tjeneste, request)

    # 110 (M-42): kontoregisteret. To leseveier og fem skriveveier.
    #
    # OG DET FINNES INGEN SPERREVEI. To av tre bransjemaler navngir
    # modulen som `v_kontovakt` og bruker `svindelsjekk_bestatt` til å
    # la utgående betalinger gå automatisk. v1 SKRIVER NED; det
    # farligste en betalingsvakt kan gjøre er ikke å slippe noe gjennom
    # — det er å stoppe noe.
    def kontovakt_bilde(request: Request) -> Response:
        from . import kontovakt as kontovaktmodul
        return kontovaktmodul.kontovaktbilde(tjeneste, request)

    def kontovakt_historikk(request: Request) -> Response:
        from . import kontovakt as kontovaktmodul
        return kontovaktmodul.historikk_endepunkt(tjeneste, request)

    def kontovakt_terskler(request: Request) -> Response:
        from . import kontovakt as kontovaktmodul
        return kontovaktmodul.terskler_endepunkt(tjeneste, request)

    def kontovakt_mottaker(request: Request) -> Response:
        from . import kontovakt as kontovaktmodul
        return kontovaktmodul.registrer_mottaker_endepunkt(tjeneste,
                                                           request)

    def kontovakt_konto(request: Request) -> Response:
        from . import kontovakt as kontovaktmodul
        return kontovaktmodul.oppgi_konto_endepunkt(tjeneste, request)

    def kontovakt_verifikasjon(request: Request) -> Response:
        from . import kontovakt as kontovaktmodul
        return kontovaktmodul.verifiser_endepunkt(tjeneste, request)

    def kontovakt_aktiv(request: Request) -> Response:
        from . import kontovakt as kontovaktmodul
        return kontovaktmodul.sett_aktiv_endepunkt(tjeneste, request)

    # 111 (M-41): betalingsregisteret. To leseveier og fem skriveveier.
    #
    # OG DET FINNES INGEN REFUSJONSVEI. Netthandelsmalen navngir
    # modulen som `v_betaling` og bruker den til å la `refusjon.utfor`
    # gå automatisk og IRREVERSIBELT opp til 5000 NOK. v1 registrerer;
    # en refusjon er penger ut døra.
    def betaling_bilde(request: Request) -> Response:
        from . import betaling as betalingmodul
        return betalingmodul.betalingsbilde(tjeneste, request)

    def betaling_historikk(request: Request) -> Response:
        from . import betaling as betalingmodul
        return betalingmodul.historikk_endepunkt(tjeneste, request)

    def betaling_terskler(request: Request) -> Response:
        from . import betaling as betalingmodul
        return betalingmodul.terskler_endepunkt(tjeneste, request)

    def betaling_subjekt(request: Request) -> Response:
        from . import betaling as betalingmodul
        return betalingmodul.registrer_subjekt_endepunkt(tjeneste,
                                                         request)

    def betaling_status(request: Request) -> Response:
        from . import betaling as betalingmodul
        return betalingmodul.registrer_status_endepunkt(tjeneste,
                                                        request)

    def betaling_abonnement(request: Request) -> Response:
        from . import betaling as betalingmodul
        return betalingmodul.sett_abonnement_endepunkt(tjeneste, request)

    def betaling_aktiv(request: Request) -> Response:
        from . import betaling as betalingmodul
        return betalingmodul.sett_aktiv_endepunkt(tjeneste, request)

    # 112 (M-19): adresseregisteret. Tre leseveier og fire skriveveier.
    #
    # OG DET FINNES INGEN OPPSLAGSVEI. Netthandelsmalen navngir modulen
    # som `v_adresse` og bruker den til å la M-25s
    # `ordre.bekreft_og_fakturer` gå automatisk. Den nærliggende
    # «løsningen» — et oppslag mot et adresseregister — er en utgående
    # kanal med personopplysninger i, og svaret ville uansett vært feil
    # vare: at en adresse FINNES sier ikke at pakken kommer fram.
    # M-51 (119): tilskudds- og støtteordningsvakten. MODULEN SENDER
    # INGEN SØKNAD, og et estimat kan ikke ferdigstilles uten
    # forutsetninger — begge er fravær i datamodellen, ikke sjekker.
    def tilskudd_bilde(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.tilskuddsbilde(tjeneste, request)

    def tilskudd_funn(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.funn_endepunkt(tjeneste, request)

    def tilskudd_kildeposter(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.kildeposter_endepunkt(tjeneste, request)

    def tilskudd_estimater(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.estimater_endepunkt(tjeneste, request)

    def tilskudd_poster(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.poster_endepunkt(tjeneste, request)

    def tilskudd_forutsetninger(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.forutsetninger_endepunkt(tjeneste,
                                                      request)

    def tilskudd_krav(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.krav_endepunkt(tjeneste, request)

    def tilskudd_ordning(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.registrer_ordning_endepunkt(tjeneste,
                                                         request)

    def tilskudd_kildepost(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.registrer_kildepost_endepunkt(tjeneste,
                                                           request)

    def tilskudd_estimat_ny(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.opprett_estimat_endepunkt(tjeneste,
                                                       request)

    def tilskudd_post(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.legg_til_post_endepunkt(tjeneste, request)

    def tilskudd_forutsetning(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.legg_til_forutsetning_endepunkt(
            tjeneste, request)

    def tilskudd_ferdigstill(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.ferdigstill_endepunkt(tjeneste, request)

    def tilskudd_aktiv(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.sett_aktiv_endepunkt(tjeneste, request)

    def tilskudd_lukk_funn(request: Request) -> Response:
        from . import tilskudd as tilskuddmodul
        return tilskuddmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-52 (122): toll- og HS-kodeagenten. MODULEN DEKLARERER
    # INGENTING — 122 har ingen «deklarert»-kolonne — og den avgir
    # INGEN FORSLAG UTEN GRUNNLAG: døra skriver forslaget og grunnene
    # i samme setning.
    def toll_bilde(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.tollbilde(tjeneste, request)

    def toll_funn(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.funn_endepunkt(tjeneste, request)

    def toll_varenummer_les(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.varenummer_endepunkt(tjeneste, request)

    def toll_grunner(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.grunner_endepunkt(tjeneste, request)

    def toll_forslag_les(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.forslag_endepunkt(tjeneste, request)

    def toll_krav(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.krav_endepunkt(tjeneste, request)

    def toll_nomenklatur(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.registrer_nomenklatur_endepunkt(tjeneste,
                                                          request)

    def toll_gyldig_til(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.sett_gyldig_til_endepunkt(tjeneste, request)

    def toll_varenummer_ny(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.registrer_varenummer_endepunkt(tjeneste,
                                                         request)

    def toll_vare(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.registrer_vare_endepunkt(tjeneste, request)

    def toll_forslag_ny(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.avgi_forslag_endepunkt(tjeneste, request)

    def toll_klart(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.merk_klart_endepunkt(tjeneste, request)

    def toll_lukk_funn(request: Request) -> Response:
        from . import tollkode as tollmodul
        return tollmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-50 (124): postjournal- og innsynsvakten. MODULEN HENTER
    # INGENTING — 124 har ingen `hentet_automatisk` og ingen utgående
    # vei. Postjournaler ER offentlige; det som treffer er at ti tusen
    # oppslag sammenstilt i et register er en PROFIL, og profilen er
    # vår, ikke kommunens.
    def journ_bilde(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.journalbilde(tjeneste, request)

    def journ_kilder(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.kilder_endepunkt(tjeneste, request)

    def journ_poster(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.poster_endepunkt(tjeneste, request)

    def journ_personer(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.personer_endepunkt(tjeneste, request)

    def journ_funn(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.funn_endepunkt(tjeneste, request)

    def journ_krav(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.krav_endepunkt(tjeneste, request)

    def journ_kilde_ny(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.registrer_kilde_endepunkt(tjeneste, request)

    def journ_gyldig_til(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.sett_gyldig_til_endepunkt(tjeneste, request)

    def journ_sak(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.opprett_sak_endepunkt(tjeneste, request)

    def journ_post_ny(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.registrer_post_endepunkt(tjeneste, request)

    def journ_anonymiser(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.anonymiser_endepunkt(tjeneste, request)

    def journ_lukk_funn(request: Request) -> Response:
        from . import postjournal as journmodul
        return journmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-47 (123): myndighetsrapporteringsagenten. MODULEN SENDER INGEN
    # INNSENDING — 123 har ingen mottaker og ingen utboks — men her er
    # FRAVÆRET IKKE NOK: en frist som går uten innsending er nøyaktig
    # det modulen ble bygget for å hindre. En stille M-47 er verre enn
    # ingen M-47.
    # M-15 LIKVIDITETS- OG KOSTNADSAGENT (128). DET FINNES INGEN RUTE
    # SOM SIER OPP NOE, OG INGEN SOM BETALER. Et kostnadstiltak kan
    # bli `vurdert` eller `avvist` av et menneske, og der stopper
    # modulen — oppsigelsen går gjennom M-41s policykontrollerte vei.
    #
    # `/maaling` ER DEN ENESTE VEIEN TIL Å LUKKE `prognose_uten_maaling`,
    # klyngens funn ingen kan klikke bort.
    def likv_bilde(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.likviditetsbilde(tjeneste, request)

    def likv_prognoser(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.prognoser_endepunkt(tjeneste, request)

    def likv_bane(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.bane_endepunkt(tjeneste, request)

    def likv_poster(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.poster_endepunkt(tjeneste, request)

    def likv_modeller(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.modeller_endepunkt(tjeneste, request)

    def likv_tiltak(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.tiltak_endepunkt(tjeneste, request)

    def likv_funn(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.funn_endepunkt(tjeneste, request)

    def likv_krav(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.krav_endepunkt(tjeneste, request)

    def likv_modell_ny(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.registrer_modell_endepunkt(tjeneste, request)

    def likv_post_ny(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.registrer_post_endepunkt(tjeneste, request)

    def likv_prognose_ny(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.lag_prognose_endepunkt(tjeneste, request)

    def likv_maaling(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.registrer_maaling_endepunkt(tjeneste, request)

    def likv_tiltak_ny(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.foresla_tiltak_endepunkt(tjeneste, request)

    def likv_vurder(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.vurder_tiltak_endepunkt(tjeneste, request)

    def likv_lukk_funn(request: Request) -> Response:
        from . import likviditet as likvmodul
        return likvmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-33 PREDIKSJONS- OG SCENARIOAGENT (130). DET FINNES INGEN RUTE
    # SOM ANSETTER, SIER OPP ELLER FLYTTER EN VAKT.
    #
    # Vaktsetningen er «prognoser er ikke fakta; ingen
    # personalavgjørelse eller automatisk handling uten separat
    # policy», og fraværet av en slik rute er hele håndhevelsen: det
    # finnes ingen tabell for beslutninger, ingen status som kan bli
    # `iverksatt`, og ingen kolonne som peker på en ansatt.
    #
    # `/maaling` ER DEN ENESTE VEIEN TIL Å LUKKE `prognose_uten_maaling`
    # — og ingen vei lukker `slaar_ikke_naiv_baseline`. Den lukkes av
    # at modellen faktisk blir bedre.
    def prog_bilde(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.prognosebilde(tjeneste, request)

    def prog_prognoser(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.prognoser_endepunkt(tjeneste, request)

    def prog_modeller(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.modeller_endepunkt(tjeneste, request)

    def prog_funn(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.funn_endepunkt(tjeneste, request)

    def prog_bane(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.bane_endepunkt(tjeneste, request)

    def prog_krav(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.krav_endepunkt(tjeneste, request)

    def prog_modell_ny(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.registrer_modell_endepunkt(tjeneste, request)

    def prog_modell_avvikle(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.avvikle_modell_endepunkt(tjeneste, request)

    def prog_prognose_ny(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.lag_prognose_endepunkt(tjeneste, request)

    def prog_maaling(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.registrer_maaling_endepunkt(tjeneste, request)

    def prog_lukk_funn(request: Request) -> Response:
        from . import prognose as progmodul
        return progmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-36 BEDRIFTSOPTIMALISATOR (132). DET FINNES INGEN RUTE SOM
    # IVERKSETTER ET TILTAK, OG INGEN SOM ENDRER EN POLICY.
    #
    # Vaktsetningen er «kan aldri utvide egen fullmakt», og den er en
    # ADVARSEL: en optimalisator som finner at den beste forbedringen
    # er «gi M-36 lov til X», gjør nøyaktig det den ble bedt om.
    # Derfor finnes det ingen rute mot `policyer`, `policyutkast`
    # eller `policyaktivering` — og `tiltaksforslag.status` har ingen
    # `iverksatt`.
    #
    # `/rangering` NEKTER med aktiv porteføljestopp. Det er stoppens
    # hele virkning, og den eneste M-36 lovlig kan ha.
    def opti_bilde(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.optimalisatorbilde(tjeneste, request)

    def opti_rangeringer(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.rangeringer_endepunkt(tjeneste, request)

    def opti_tiltak(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.tiltak_endepunkt(tjeneste, request)

    def opti_modeller(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.modeller_endepunkt(tjeneste, request)

    def opti_funn(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.funn_endepunkt(tjeneste, request)

    def opti_signaler(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.signaler_endepunkt(tjeneste, request)

    def opti_rangering(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.rangering_endepunkt(tjeneste, request)

    def opti_krav(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.krav_endepunkt(tjeneste, request)

    def opti_modell_ny(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.registrer_modell_endepunkt(tjeneste, request)

    def opti_modell_avvikle(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.avvikle_modell_endepunkt(tjeneste, request)

    def opti_tiltak_ny(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.foresla_tiltak_endepunkt(tjeneste, request)

    def opti_vurder(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.vurder_tiltak_endepunkt(tjeneste, request)

    def opti_stopp_ny(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.sett_stopp_endepunkt(tjeneste, request)

    def opti_stopp_opphev(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.opphev_stopp_endepunkt(tjeneste, request)

    def opti_rangere(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.rangere_endepunkt(tjeneste, request)

    def opti_effekt(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.registrer_effekt_endepunkt(tjeneste, request)

    def opti_lukk_funn(request: Request) -> Response:
        from . import optimalisator as optimodul
        return optimodul.lukk_funn_endepunkt(tjeneste, request)

    # M-7 MØTEOPERASJONSAGENT (133). DET FINNES INGEN RUTE SOM FATTER
    # EN BESLUTNING.
    #
    # `/beslutning` KREVER `besluttet_av`, og kolonnen er NOT NULL i
    # basen: en beslutning uten et menneske bak er ikke en beslutning
    # modulen skrev ned — det er en beslutning modulen FATTET.
    #
    # `/opptak` ER DEN ENESTE HANDLINGEN I MODULEN SOM IKKE KAN GJØRES
    # UGJORT, og døra nekter på fire ting FØR raden finnes: manglende
    # hjemmel, utløpt hjemmel, ingen varslet, og varsling som kom etter
    # at opptaket startet. ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE
    # ET NEKT.
    def mote_bilde(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.motebilde(tjeneste, request)

    def mote_moter(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.moter_endepunkt(tjeneste, request)

    def mote_hjemler(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.hjemler_endepunkt(tjeneste, request)

    def mote_aksjoner(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.aksjoner_endepunkt(tjeneste, request)

    def mote_funn(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.funn_endepunkt(tjeneste, request)

    def mote_referat(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.referat_endepunkt(tjeneste, request)

    def mote_krav(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.krav_endepunkt(tjeneste, request)

    def mote_hjemmel_ny(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.registrer_hjemmel_endepunkt(tjeneste, request)

    def mote_hjemmel_avslutt(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.avslutt_hjemmel_endepunkt(tjeneste, request)

    def mote_mote_ny(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.registrer_mote_endepunkt(tjeneste, request)

    def mote_opptak(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.start_opptak_endepunkt(tjeneste, request)

    def mote_punkt(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.registrer_referatpunkt_endepunkt(
            tjeneste, request)

    def mote_beslutning(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.registrer_beslutning_endepunkt(
            tjeneste, request)

    def mote_aksjon_ny(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.registrer_aksjon_endepunkt(tjeneste, request)

    def mote_aksjon_lukk(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.lukk_aksjon_endepunkt(tjeneste, request)

    def mote_lukk_funn(request: Request) -> Response:
        from . import moteoperasjon as motemodul
        return motemodul.lukk_funn_endepunkt(tjeneste, request)

    # M-20 INNHOLDSAGENT (134). DET FINNES INGEN RUTE SOM PUBLISERER
    # PÅ EGEN HÅND.
    #
    # `/publiser` KREVER `publisert_av`, og kolonnen er NOT NULL i
    # basen: en publisering uten et menneske bak er ikke en publisering
    # modulen skrev ned — det er en publisering modulen GJORDE.
    #
    # DØRA NEKTER PÅ FEM TING FØR RADEN FINNES: utkastet er ikke klart,
    # forhåndsvisningen gjelder et annet utkast, summene spriker,
    # visningen er for gammel, eller en påstand hviler på en utløpt
    # kilde. EN ROLLBACK FJERNER SIDEN — DEN FJERNER IKKE AT NOEN LESTE
    # DEN, og derfor må veien tilbake finnes FØR veien fram tas.
    #
    # `/kilde` SKRIVER I HUSETS KILDEREGISTER (M-46/118), ikke i et
    # eget: to kilderegistre ville gitt to svar på «kan vi belegge
    # dette».
    def innhold_bilde(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.innholdsbilde(tjeneste, request)

    def innhold_sider(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.sider_endepunkt(tjeneste, request)

    def innhold_kilder(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.kilder_endepunkt(tjeneste, request)

    def innhold_funn(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.funn_endepunkt(tjeneste, request)

    def innhold_utkastet(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.utkast_endepunkt(tjeneste, request)

    def innhold_krav(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.krav_endepunkt(tjeneste, request)

    def innhold_kilde_ny(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.registrer_kilde_endepunkt(tjeneste, request)

    def innhold_utkast_ny(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.registrer_utkast_endepunkt(tjeneste, request)

    def innhold_paastand(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.registrer_paastand_endepunkt(tjeneste,
                                                          request)

    def innhold_visning(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.registrer_visning_endepunkt(tjeneste,
                                                         request)

    def innhold_klar(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.merk_klar_endepunkt(tjeneste, request)

    def innhold_publiser(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.publiser_endepunkt(tjeneste, request)

    def innhold_tilbake(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.rull_tilbake_endepunkt(tjeneste, request)

    def innhold_lukk_funn(request: Request) -> Response:
        from . import innhold as innholdsmodul
        return innholdsmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-43 TALE- OG TELEFONIAGENT (135). DET FINNES INGEN RUTE SOM
    # INNGÅR EN AVTALE ELLER LOVER PENGER.
    #
    # Vaktsetningen krever eksplisitt policy for begge, og v1 har ingen
    # vei dit i det hele tatt — ikke en avslått vei, ikke en vei bak en
    # bryter. Det finnes ingen kropp med et beløp i.
    #
    # `/samtale` NEKTER hvis identifikasjonen er datert før samtalen
    # startet, eller kom senere enn tenantens frist. DEN SOM TROR HUN
    # SNAKKER MED ET MENNESKE, SVARER ANNERLEDES.
    #
    # `/linje` NEKTER en linje datert før identifikasjonen: INGENTING
    # BLE SAGT FØR VI SA HVA VI ER.
    #
    # `/opptak` arver 133s fire nekt ordrett, og gyldigheten måles med
    # M-7s egen funksjon — ikke med en kopi.
    def telefoni_bilde(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.telefonibilde(tjeneste, request)

    def telefoni_samtaler(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.samtaler_endepunkt(tjeneste, request)

    def telefoni_hjemler(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.hjemler_endepunkt(tjeneste, request)

    def telefoni_regler(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.regler_endepunkt(tjeneste, request)

    def telefoni_funn(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.funn_endepunkt(tjeneste, request)

    def telefoni_transkripsjon(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.transkripsjon_endepunkt(tjeneste, request)

    def telefoni_krav(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.krav_endepunkt(tjeneste, request)

    def telefoni_hjemmel_ny(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.registrer_hjemmel_endepunkt(tjeneste, request)

    def telefoni_regel_ny(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.registrer_regel_endepunkt(tjeneste, request)

    def telefoni_regel_avvikle(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.avvikle_regel_endepunkt(tjeneste, request)

    def telefoni_samtale_ny(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.start_samtale_endepunkt(tjeneste, request)

    def telefoni_samtale_avslutt(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.avslutt_samtale_endepunkt(tjeneste, request)

    def telefoni_opptak(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.start_opptak_endepunkt(tjeneste, request)

    def telefoni_linje(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.registrer_linje_endepunkt(tjeneste, request)

    def telefoni_eskaler(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.eskaler_endepunkt(tjeneste, request)

    def telefoni_eskalering_lukk(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.lukk_eskalering_endepunkt(tjeneste, request)

    def telefoni_lukk_funn(request: Request) -> Response:
        from . import telefoni as telefonimodul
        return telefonimodul.lukk_funn_endepunkt(tjeneste, request)

    # M-45 BÆREKRAFTS- OG ESG-AGENT (136). DET FINNES INGEN RUTE SOM
    # SENDER RAPPORTEN.
    #
    # `/sammenstill` samler tallene og skriver en ny rad. Det er alt.
    # Innsendingen til et tilsyn er et menneskes, og den hører hjemme i
    # M-47 — en rute her ville gjort «sendte vi?» til et spørsmål med
    # to svar.
    #
    # `/maaling` NEKTER på seks ting: ukjent periode, LUKKET periode,
    # ukjent faktor, faktor fra en ANNEN STANDARDVERSJON enn perioden,
    # faktor som ikke gjaldt i perioden, og utløpt kilde. ET TALL
    # REGNET MED FJORÅRETS FAKTOR OG LEST SOM ÅRETS ER FEIL PÅ NØYAKTIG
    # DEN MÅTEN CSRD SKAL HINDRE.
    #
    # `er_estimat` er påkrevd og har ingen default: en glemt kolonne
    # ville blitt en FALSK PÅSTAND i stedet for en feil.
    def esg_bilde(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.esgbilde(tjeneste, request)

    def esg_perioder(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.perioder_endepunkt(tjeneste, request)

    def esg_faktorer(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.faktorer_endepunkt(tjeneste, request)

    def esg_funn(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.funn_endepunkt(tjeneste, request)

    def esg_maalinger(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.maalinger_endepunkt(tjeneste, request)

    def esg_paastander(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.paastander_endepunkt(tjeneste, request)

    def esg_krav(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.krav_endepunkt(tjeneste, request)

    def esg_kilde_ny(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.registrer_kilde_endepunkt(tjeneste, request)

    def esg_periode_ny(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.apne_periode_endepunkt(tjeneste, request)

    def esg_periode_lukk(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.lukk_periode_endepunkt(tjeneste, request)

    def esg_faktor_ny(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.registrer_faktor_endepunkt(tjeneste, request)

    def esg_faktor_avvikle(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.avvikle_faktor_endepunkt(tjeneste, request)

    def esg_maaling_ny(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.registrer_maaling_endepunkt(tjeneste, request)

    def esg_paastand_ny(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.registrer_paastand_endepunkt(tjeneste, request)

    def esg_sammenstill(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.sammenstill_endepunkt(tjeneste, request)

    def esg_lukk_funn(request: Request) -> Response:
        from . import esg as esgmodul
        return esgmodul.lukk_funn_endepunkt(tjeneste, request)




    # M-53 HMS- OG AVVIKSMOTTAK (127). DET FINNES INGEN RUTE SOM
    # VARSLER EN MYNDIGHET, og ingen som lukker et avvik uten et
    # tiltak å vise til.
    #
    # `/avvik` er den eneste ruten i hele API-et som med vilje IKKE
    # sender aktøren videre. For et anonymt avvik stanser bruker-id-en
    # her: `revisjonslogg` er append-only siden 001, og et navn som
    # lekker inn der kan aldri fjernes igjen.
    def hms_bilde(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.hmsbilde(tjeneste, request)

    def hms_avvik(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.avvik_endepunkt(tjeneste, request)

    def hms_regelverk(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.regelverk_endepunkt(tjeneste, request)

    def hms_funn(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.funn_endepunkt(tjeneste, request)

    def hms_tiltak(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.tiltak_endepunkt(tjeneste, request)

    def hms_grunnlag(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.grunnlag_endepunkt(tjeneste, request)

    def hms_krav(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.krav_endepunkt(tjeneste, request)

    def hms_regel_ny(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.registrer_regel_endepunkt(tjeneste, request)

    def hms_meld(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.meld_avvik_endepunkt(tjeneste, request)

    def hms_tiltak_ny(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.registrer_tiltak_endepunkt(tjeneste, request)

    def hms_anonymiser(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.anonymiser_endepunkt(tjeneste, request)

    def hms_lukk_funn(request: Request) -> Response:
        from . import hms as hmsmodul
        return hmsmodul.lukk_funn_endepunkt(tjeneste, request)

    def mynd_bilde(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.myndighetsbilde(tjeneste, request)

    def mynd_regelverk_les(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.regelverk_endepunkt(tjeneste, request)

    def mynd_plikter_les(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.plikter_endepunkt(tjeneste, request)

    def mynd_funn(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.funn_endepunkt(tjeneste, request)

    def mynd_krav(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.krav_endepunkt(tjeneste, request)

    def mynd_regelverk_ny(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.registrer_regelverk_endepunkt(tjeneste,
                                                       request)

    def mynd_gyldig_til(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.sett_gyldig_til_endepunkt(tjeneste, request)

    def mynd_plikttype(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.registrer_plikttype_endepunkt(tjeneste,
                                                       request)

    def mynd_plikt_ny(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.registrer_plikt_endepunkt(tjeneste, request)

    def mynd_bevis(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.registrer_bevis_endepunkt(tjeneste, request)

    def mynd_lukk_funn(request: Request) -> Response:
        from . import myndighetsrapport as myndmodul
        return myndmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-54 (121): EHF- og Peppol-avviksretteren. MODULEN SENDER INGEN
    # FAKTURA — 121 har ingen mottaker og ingen utboks — og den
    # VALIDERER IKKE MOT ET UTLØPT REGELSETT: en dom felt under en
    # foreldet regel ser velformet ut og er gal.
    def ehf_bilde(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.ehfbilde(tjeneste, request)

    def ehf_funn(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.funn_endepunkt(tjeneste, request)

    def ehf_regler(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.regler_endepunkt(tjeneste, request)

    def ehf_avvik(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.avvik_endepunkt(tjeneste, request)

    def ehf_valideringer(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.valideringer_endepunkt(tjeneste, request)

    def ehf_krav(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.krav_endepunkt(tjeneste, request)

    def ehf_regelsett(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.registrer_regelsett_endepunkt(tjeneste,
                                                      request)

    def ehf_gyldig_til(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.sett_gyldig_til_endepunkt(tjeneste, request)

    def ehf_regel(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.registrer_regel_endepunkt(tjeneste, request)

    def ehf_dokument(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.registrer_dokument_endepunkt(tjeneste,
                                                     request)

    def ehf_felter(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.registrer_felter_endepunkt(tjeneste, request)

    def ehf_valider(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.valider_endepunkt(tjeneste, request)

    def ehf_retting(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.registrer_retting_endepunkt(tjeneste, request)

    def ehf_klar(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.merk_klar_endepunkt(tjeneste, request)

    def ehf_lukk_funn(request: Request) -> Response:
        from . import ehf as ehfmodul
        return ehfmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-55 (120): merkevare- og IP-overvåkeren. MODULEN SENDER INGEN
    # KRAV OG INGEN KLAGE, og hvert funn peker på en bevaringskopi —
    # begge er fravær i datamodellen, ikke sjekker. Modulens eneste
    # utgang er `/henvis`, som fester en peker til M-37s unntakskø.
    def merkevare_bilde(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.merkevarebilde(tjeneste, request)

    def merkevare_alle_funn(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.alle_funn_endepunkt(tjeneste, request)

    def merkevare_kopier(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.bevaringskopier_endepunkt(tjeneste,
                                                        request)

    def merkevare_varsler(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.varsler_endepunkt(tjeneste, request)

    def merkevare_funn_for_merke(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.funn_endepunkt(tjeneste, request)

    def merkevare_vurderinger(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.vurderinger_endepunkt(tjeneste, request)

    def merkevare_krav(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.krav_endepunkt(tjeneste, request)

    def merkevare_merke(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.registrer_merkevare_endepunkt(tjeneste,
                                                            request)

    def merkevare_kopi(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.registrer_kopi_endepunkt(tjeneste,
                                                       request)

    def merkevare_funn_ny(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.registrer_funn_endepunkt(tjeneste,
                                                       request)

    def merkevare_vurder(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.vurder_endepunkt(tjeneste, request)

    def merkevare_henvis(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.henvis_endepunkt(tjeneste, request)

    def merkevare_lukk_funn(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.lukk_funn_endepunkt(tjeneste, request)

    def merkevare_aktiv(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.sett_merke_aktiv_endepunkt(tjeneste,
                                                         request)

    def merkevare_lukk_varsel(request: Request) -> Response:
        from . import merkevare as merkevaremodul
        return merkevaremodul.lukk_varsel_endepunkt(tjeneste, request)

    # M-46 (118): anbuds- og konkurransevakten. MODULEN SENDER
    # INGEN TILBUD, og hvert faktapunkt i et utkast peker på et
    # kildedokument — begge er fravær i datamodellen, ikke sjekker.
    def anbud_bilde(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.anbudsbilde(tjeneste, request)

    def anbud_funn(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.funn_endepunkt(tjeneste, request)

    def anbud_kilder(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.kilder_endepunkt(tjeneste, request)

    def anbud_krav_les(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.krav_endepunkt(tjeneste, request)

    def anbud_utkast_les(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.utkast_endepunkt(tjeneste, request)

    def anbud_profil(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.profil_endepunkt(tjeneste, request)

    def anbud_registrer(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.registrer_anbud_endepunkt(tjeneste, request)

    def anbud_krav_ny(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.registrer_krav_endepunkt(tjeneste, request)

    def anbud_kilde_ny(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.registrer_kilde_endepunkt(tjeneste, request)

    def anbud_utkast_ny(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.opprett_utkast_endepunkt(tjeneste, request)

    def anbud_punkt(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.registrer_punkt_endepunkt(tjeneste, request)

    def anbud_klart(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.merk_klart_endepunkt(tjeneste, request)

    def anbud_aktiv(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.sett_aktiv_endepunkt(tjeneste, request)

    def anbud_lukk_funn(request: Request) -> Response:
        from . import anbud as anbudmodul
        return anbudmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-49 (117): sanksjonskontrollen. MODULEN BLOKKERER INGENTING
    # og AVFEIER INGEN NAVNELIKHET — se `sanksjon.py` og toppen av
    # migrasjon 117 for beslutningen, motargumentet og utløseren.
    def sanksjon_bilde(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.sanksjonsbilde(tjeneste, request)

    def sanksjon_funn(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.funn_endepunkt(tjeneste, request)

    def sanksjon_lister(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.lister_endepunkt(tjeneste, request)

    def sanksjon_kontroller(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.kontroller_endepunkt(tjeneste, request)

    def sanksjon_treff(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.treff_endepunkt(tjeneste, request)

    def sanksjon_krav(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.krav_endepunkt(tjeneste, request)

    def sanksjon_liste(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.registrer_liste_endepunkt(tjeneste,
                                                       request)

    def sanksjon_subjekt(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.registrer_subjekt_endepunkt(tjeneste,
                                                         request)

    def sanksjon_kontroll(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.registrer_kontroll_endepunkt(tjeneste,
                                                          request)

    def sanksjon_avklaring(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.avklar_endepunkt(tjeneste, request)

    def sanksjon_aktiv(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.sett_aktiv_endepunkt(tjeneste, request)

    def sanksjon_lukk_funn(request: Request) -> Response:
        from . import sanksjon as sanksjonmodul
        return sanksjonmodul.lukk_funn_endepunkt(tjeneste, request)

    # M-48 (116): klyngens ENE utgående kanal. Foretaksregisteret er
    # koblet på; kredittleverandøren står bak
    # `modulen_hentet_kredittdata` og finnes ikke i koden.
    def motpart_bilde(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.motpartsbilde(tjeneste, request)

    def motpart_funn(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.funn_endepunkt(tjeneste, request)

    def motpart_historikk(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.historikk_endepunkt(tjeneste, request)

    def motpart_oppslagslogg(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.oppslagslogg_endepunkt(tjeneste, request)

    def motpart_krav(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.krav_endepunkt(tjeneste, request)

    def motpart_registrer(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.registrer_motpart_endepunkt(tjeneste,
                                                        request)

    def motpart_oppslag(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.oppslag_endepunkt(tjeneste, request)

    def motpart_vurdering(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.vurdering_endepunkt(tjeneste, request)

    def motpart_deaktiver(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.deaktiver_endepunkt(tjeneste, request)

    def motpart_lukk_funn(request: Request) -> Response:
        from . import motpart as motpartmodul
        return motpartmodul.lukk_funn_endepunkt(tjeneste, request)

    def adresse_bilde(request: Request) -> Response:
        from . import adresse as adressemodul
        return adressemodul.adressebilde(tjeneste, request)

    def adresse_historikk(request: Request) -> Response:
        from . import adresse as adressemodul
        return adressemodul.historikk_endepunkt(tjeneste, request)

    def adresse_kontroller(request: Request) -> Response:
        from . import adresse as adressemodul
        return adressemodul.kontroller_endepunkt(tjeneste, request)

    def adresse_krav(request: Request) -> Response:
        from . import adresse as adressemodul
        return adressemodul.krav_endepunkt(tjeneste, request)

    def adresse_subjekt(request: Request) -> Response:
        from . import adresse as adressemodul
        return adressemodul.registrer_subjekt_endepunkt(tjeneste,
                                                        request)

    def adresse_versjon(request: Request) -> Response:
        from . import adresse as adressemodul
        return adressemodul.registrer_adresse_endepunkt(tjeneste,
                                                        request)

    def adresse_kontroll(request: Request) -> Response:
        from . import adresse as adressemodul
        return adressemodul.registrer_kontroll_endepunkt(tjeneste,
                                                         request)

    def adresse_aktiv(request: Request) -> Response:
        from . import adresse as adressemodul
        return adressemodul.sett_aktiv_endepunkt(tjeneste, request)

    # 113 (M-39): lønnsgrunnlaget. Fire leseveier og fem skriveveier.
    #
    # OG DET FINNES INGEN UTBETALINGSVEI OG INGEN EKSPORTVEI.
    # Håndverk/bygg-malen navngir modulen som `v_lonn` og bruker ALLE
    # TRE vilkårene den er betrodd for til å la
    # `timeliste.samle_og_valider` gå automatisk. En lønnsfil er ikke en
    # betaling — det er en fil, den ser harmløs ut, og den rammer alle
    # på én gang.
    def lonn_bilde(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.lonnsbilde(tjeneste, request)

    def lonn_dager(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.dager_endepunkt(tjeneste, request)

    def lonn_historikk(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.historikk_endepunkt(tjeneste, request)

    def lonn_planer(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.planer_endepunkt(tjeneste, request)

    def lonn_terskler(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.terskler_endepunkt(tjeneste, request)

    def lonn_taker(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.registrer_taker_endepunkt(tjeneste, request)

    def lonn_plan(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.sett_plan_endepunkt(tjeneste, request)

    def lonn_timer(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.registrer_timer_endepunkt(tjeneste, request)

    def lonn_aktiv(request: Request) -> Response:
        from . import lonn as lonnmodul
        return lonnmodul.sett_aktiv_endepunkt(tjeneste, request)

    # 114 (M-44): kampanjeregisteret. Fire leseveier og seks
    # skriveveier.
    #
    # OG DET FINNES INGEN UTSENDINGSVEI. M-44 er en annen figur enn de
    # tre andre i klynge 5: de er manglende VERIFIKATORER, denne er den
    # manglende AKTØREN — malen fører modulen som `modul:` på en
    # `auto`-handling. Modulen finnes FOR å sende, og v1 sender null.
    def kampanje_bilde(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.kampanjebilde(tjeneste, request)

    def kampanje_historikk(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.historikk_endepunkt(tjeneste, request)

    def kampanje_samtykke_dato(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.samtykke_paa_dato_endepunkt(tjeneste,
                                                         request)

    def kampanje_grense(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.grense_endepunkt(tjeneste, request)

    def kampanje_mottaker(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.registrer_mottaker_endepunkt(tjeneste,
                                                          request)

    def kampanje_samtykke(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.registrer_samtykke_endepunkt(tjeneste,
                                                          request)

    def kampanje_ny(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.registrer_kampanje_endepunkt(tjeneste,
                                                          request)

    def kampanje_avlys(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.avlys_kampanje_endepunkt(tjeneste, request)

    def kampanje_plan(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.legg_i_plan_endepunkt(tjeneste, request)

    def kampanje_aktiv(request: Request) -> Response:
        from . import kampanje as kampanjemodul
        return kampanjemodul.sett_aktiv_endepunkt(tjeneste, request)

    # 097 (M-12): tilgangsregisteret. Leseveien er tenantens eget
    # register OG de åpne funnene i ett kall; de tre skriveveiene er
    # menneskelige registreringer i flaten. INGEN av dem provisjonerer
    # noe — v1 registrerer hvem som har hvilken tilgang til hva, og
    # gjør avvik synlige.
    def tilgang_liste(request: Request) -> Response:
        from . import tilgang as tilgangmodul
        return tilgangmodul.tilgangsbilde(tjeneste, request)

    def tilgang_objekt(request: Request) -> Response:
        from . import tilgang as tilgangmodul
        return tilgangmodul.registrer_objekt_endepunkt(tjeneste, request)

    def tilgang_registrer(request: Request) -> Response:
        from . import tilgang as tilgangmodul
        return tilgangmodul.registrer_tilgang_endepunkt(tjeneste, request)

    def tilgang_gjennomgang(request: Request) -> Response:
        from . import tilgang as tilgangmodul
        return tilgangmodul.registrer_gjennomgang_endepunkt(tjeneste,
                                                            request)
    # 098 (M-22): lisensregisteret. Samme snitt som 096 over: leseveien er
    # tenantens eget register; de tre skriveveiene er MENNESKELIGE
    # handlinger i flaten, bak `bestilling:opprett` + CSRF +
    # Idempotency-Key. Sveipen som køer utløpsvarslene finnes IKKE som
    # HTTP — den er et forpass i varselsenderen, med sitt eget grant til
    # `disponit_varselsender`. Og det finnes ingen oppsigelsesvei her i
    # det hele tatt: `avslutt` er et menneske som FØRER at lisensen er
    # avsluttet, ikke modulen som avslutter den.
    def lisens_liste(request: Request) -> Response:
        from . import lisens as lisensmodul
        return lisensmodul.lisenser(tjeneste, request)

    def lisens_registrer(request: Request) -> Response:
        from . import lisens as lisensmodul
        return lisensmodul.registrer_endepunkt(tjeneste, request)

    def lisens_fornyelse(request: Request) -> Response:
        from . import lisens as lisensmodul
        return lisensmodul.fornyelse_endepunkt(tjeneste, request)

    def lisens_avslutt(request: Request) -> Response:
        from . import lisens as lisensmodul
        return lisensmodul.avslutt_endepunkt(tjeneste, request)
    # 099 (M-30): forespørselsregisteret. Leseveien er tenantens eget
    # register bak compliance-lesescopet; de fire skriveveiene er
    # menneskelige handlinger i flaten, bak `bestilling:opprett` + CSRF
    # + Idempotency-Key. Fristsveipen finnes IKKE som HTTP — den er
    # kryss-tenant og kjøres av `disponit_personvernsveip` fra sin egen
    # timer, med sitt eget grant. Og INGEN av rutene sletter noe:
    # sletting eies av M-4s retensjonsregnskap (093).
    def personvern_liste(request: Request) -> Response:
        from . import personvern as personvernmodul
        return personvernmodul.saker(tjeneste, request)

    def personvern_registrer(request: Request) -> Response:
        from . import personvern as personvernmodul
        return personvernmodul.registrer_endepunkt(tjeneste, request)

    def personvern_svar(request: Request) -> Response:
        from . import personvern as personvernmodul
        return personvernmodul.besvar_endepunkt(tjeneste, request)

    def personvern_avvis(request: Request) -> Response:
        from . import personvern as personvernmodul
        return personvernmodul.avvis_endepunkt(tjeneste, request)

    def personvern_forleng(request: Request) -> Response:
        from . import personvern as personvernmodul
        return personvernmodul.forleng_endepunkt(tjeneste, request)

    def drift_backup(request: Request) -> Response:
        return lesing.drift_backup(tjeneste, request)

    def drift_selvtest(request: Request) -> Response:
        return lesing.drift_selvtest(tjeneste, request)

    def datakvalitet(request: Request) -> Response:
        return lesing.datakvalitet(tjeneste, request)
    def retensjon(request: Request) -> Response:
        return lesing.retensjon(tjeneste, request)
    # M-9 (095): begrepsregisteret. Rent lesende — ordlisten fylles
    # gjennom de eier-eide dørene i 095, som `migrer.py` REVOKEr fra
    # runtime-rollen. Det er en sikkerhetsdom (M-31-formen), ikke en
    # manglende funksjon.
    def kunnskap(request: Request) -> Response:
        return lesing.kunnskap(tjeneste, request)

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

    # #162: inndata-artefaktet — buntens vei inn (PR-1: reservasjon +
    # opplasting; resolver og bestillingsbinding er PR-2).
    from . import inndata as inndata_http

    def inndata_reserver(request: Request) -> Response:
        return inndata_http.reserver_endepunkt(tjeneste, request)

    async def inndata_hent(request: Request) -> Response:
        # Kroppen (owner_claim_id-kapabiliteten) leses async; selve
        # arbeidet (pool, dekryptering, fil) går i threadpoolen som de
        # andre sync-veiene.
        from starlette.concurrency import run_in_threadpool
        try:
            kropp = await request.json()
        except Exception:
            kropp = None
        return await run_in_threadpool(
            inndata_http.hent_endepunkt, tjeneste, request, kropp)

    async def inndata_opplast(request: Request) -> Response:
        return await inndata_http.opplast_endepunkt(tjeneste, request)

    # M-57 utførelsesarmen: leseflaten + signeringen gjennom 056-kjeden.
    from . import rekruttering as rekruttering_http

    def rekruttering_prosesser(request: Request) -> Response:
        return rekruttering_http.prosesser_endepunkt(tjeneste, request)

    def rekruttering_kandidatkort(request: Request) -> Response:
        return rekruttering_http.kandidatkort_endepunkt(tjeneste, request)

    def rekruttering_profil_slett(request: Request) -> Response:
        return rekruttering_http.stillingsprofil_slett_endepunkt(
            tjeneste, request)

    def rekruttering_liste_opprett(request: Request) -> Response:
        return rekruttering_http.liste_opprett_endepunkt(tjeneste, request)

    # M-8 tidsvalg (082): kundens slot-administrasjon ...
    def rekruttering_tidsvalg(request: Request) -> Response:
        return rekruttering_http.tidsvalg_endepunkt(tjeneste, request)

    def rekruttering_tidsvalg_slots(request: Request) -> Response:
        return rekruttering_http.tidsvalg_slots_endepunkt(tjeneste, request)

    def rekruttering_tidsvalg_deaktiver(request: Request) -> Response:
        return rekruttering_http.tidsvalg_slot_deaktiver_endepunkt(
            tjeneste, request)

    # ... og kandidatens offentlige dører (NY ruteklasse — uautentisert
    # utenom OIDC: kapabiliteten ER credentialet, se api/tidsvalg.py).
    from . import tidsvalg as tidsvalgmodul

    def tidsvalg_oppslag(request: Request) -> Response:
        return tidsvalgmodul.oppslag_endepunkt(tjeneste, request)

    def tidsvalg_velg(request: Request) -> Response:
        return tidsvalgmodul.velg_endepunkt(tjeneste, request)

    def tidsvalg_side(request: Request) -> Response:
        return uiserver.tidsvalg_side(request)

    # M-6 PR-B: kilderegistrering med M365-OAuth.
    from . import epost_kilde as epostkildemodul

    def epost_kilder(request: Request) -> Response:
        return epostkildemodul.liste_endepunkt(tjeneste, request)

    def epost_kilde_start(request: Request) -> Response:
        return epostkildemodul.start_endepunkt(tjeneste, request)

    def epost_kilde_callback(request: Request) -> Response:
        return epostkildemodul.callback_endepunkt(tjeneste, request)

    def epost_kilde_deaktiver(request: Request) -> Response:
        return epostkildemodul.deaktiver_endepunkt(tjeneste, request)

    def rekruttering_tekster(request: Request) -> Response:
        return rekruttering_http.utsendingstekster_endepunkt(
            tjeneste, request)

    def rekruttering_tekst_lagre(request: Request) -> Response:
        return rekruttering_http.utsendingstekst_lagre_endepunkt(
            tjeneste, request)

    def rekruttering_tekst_slett(request: Request) -> Response:
        return rekruttering_http.utsendingstekst_slett_endepunkt(
            tjeneste, request)

    def rekruttering_kandidatdokument_les(request: Request) -> Response:
        return rekruttering_http.kandidatdokument_les_endepunkt(
            tjeneste, request)

    def rekruttering_signer(request: Request) -> Response:
        return rekruttering_http.signer_endepunkt(tjeneste, request)

    def rekruttering_blinding(request: Request) -> Response:
        return rekruttering_http.blinding_endepunkt(tjeneste, request)

    def rk_slett(request: Request) -> Response:
        return rekruttering_http.evaluering_slett_endepunkt(tjeneste, request)

    def rk_avbryt(request: Request) -> Response:
        return rekruttering_http.evaluering_avbryt_endepunkt(tjeneste, request)

    def rekruttering_profiler(request: Request) -> Response:
        return rekruttering_http.stillingsprofiler_endepunkt(
            tjeneste, request)

    def rekruttering_profil_lagre(request: Request) -> Response:
        return rekruttering_http.stillingsprofil_lagre_endepunkt(
            tjeneste, request)

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

    def pa_simuler_utkast(request: Request) -> Response:
        return policyadmin_http.simuler_utkast_endepunkt(tjeneste, request)

    def pa_valider_utkast(request: Request) -> Response:
        return policyadmin_http.valider_utkast_endepunkt(tjeneste, request)

    def pa_varsel_liste(request: Request) -> Response:
        return policyadmin_http.varsel_liste_endepunkt(tjeneste, request)

    def pa_varsel_lest(request: Request) -> Response:
        return policyadmin_http.varsel_lest_endepunkt(tjeneste, request)

    def pa_varselvalg(request: Request) -> Response:
        return policyadmin_http.varselvalg_endepunkt(tjeneste, request)

    def pa_slett_policy(request: Request) -> Response:
        return policyadmin_http.slett_policy_endepunkt(tjeneste, request)

    def pa_forkast_utkast(request: Request) -> Response:
        return policyadmin_http.forkast_utkast_endepunkt(tjeneste, request)

    def pa_gjenapne_utkast(request: Request) -> Response:
        return policyadmin_http.gjenapne_utkast_endepunkt(tjeneste, request)

    def pa_apne_runde(request: Request) -> Response:
        return policyadmin_http.apne_runde_endepunkt(tjeneste, request)

    def pa_attester(request: Request) -> Response:
        return policyadmin_http.attester_endepunkt(tjeneste, request)

    # 038: bestillingsveien — kundeflatens produsent inn i beslutningsveien.
    from . import bestilling as bestillingsmodul
    from . import domener as domenermodul

    def dm_liste(request: Request) -> Response:
        return domenermodul.liste_endepunkt(tjeneste, request)

    def dm_utsted(request: Request) -> Response:
        return domenermodul.utsted_endepunkt(tjeneste, request)

    # 047: versjonshistorikk og diff — lesing gjennom policy-eierens
    # definere, aldri direkte fra policyer (port 38).
    from . import policy_historikk as ph

    def ph_versjoner(request):
        return ph.versjoner_endepunkt(tjeneste, request)

    def ph_diff(request):
        return ph.diff_endepunkt(tjeneste, request)

    def ph_grunnlag(request):
        return ph.editorgrunnlag_endepunkt(tjeneste, request)

    # 044: planflaten — CRUD over de herdede funksjonene.
    from . import plan as planmodul

    def pl_opprett(request: Request) -> Response:
        return planmodul.opprett_endepunkt(tjeneste, request)

    def pl_liste(request: Request) -> Response:
        return planmodul.liste_endepunkt(tjeneste, request)

    def pl_aktiver(request: Request) -> Response:
        return planmodul.aktiver_endepunkt(tjeneste, request)

    def pl_gjenoppta(request: Request) -> Response:
        return planmodul.gjenoppta_endepunkt(tjeneste, request)

    def pl_stans(request: Request) -> Response:
        return planmodul.stans_endepunkt(tjeneste, request)

    def pl_historikk(request: Request) -> Response:
        return planmodul.historikk_endepunkt(tjeneste, request)

    def do_saker(request: Request) -> Response:
        # 041: adjudikatorkøen — plattformens visning, aldri en kundesesjons.
        from . import domeneovertakelse
        return domeneovertakelse.saker_endepunkt(tjeneste, request)

    def bs_bestill(request: Request) -> Response:
        return bestillingsmodul.bestill_endepunkt(tjeneste, request)

    # 035: modul-onboarding — hemmelighet, innløsning, rotasjon,
    # tilbakekalling. Maskin-/ops-endepunkter (Bearer), aldri browserøkt.
    from . import modulonboarding

    def mo_utsted(request: Request) -> Response:
        return modulonboarding.utsted_endepunkt(tjeneste, request)

    def mo_innlos(request: Request) -> Response:
        return modulonboarding.innlos_endepunkt(tjeneste, request)

    def mo_roter(request: Request) -> Response:
        return modulonboarding.roter_endepunkt(tjeneste, request)

    def mo_tilbakekall(request: Request) -> Response:
        return modulonboarding.tilbakekall_endepunkt(tjeneste, request)

    app = Starlette(routes=[
        Route("/v1/beslutning", beslutning, methods=["POST"]),
        Route("/v1/unntak", unntak, methods=["GET"]),
        # PR-006: outbox-protokollen. Den syntetiske eiermodulen på staging
        # bruker NØYAKTIG disse to endepunktene og skriver aldri i
        # databasen direkte — det er en av de åtte evidensbevisene, og en
        # statisk sjekk i testsuiten håndhever den.
        Route("/v1/oppdrag/claim", oppdrag_claim, methods=["POST"]),
        # 035: onboarding-rutene er statiske stier, registrert her sammen
        # med de andre maskinrutene.
        Route("/v1/bestilling", bs_bestill, methods=["POST"]),
        Route("/v1/domener", dm_liste, methods=["GET"]),
        Route("/v1/domeneovertakelse/saker", do_saker, methods=["GET"]),
        Route("/v1/plan", pl_liste, methods=["GET"]),
        Route("/v1/plan", pl_opprett, methods=["POST"]),
        # {id:uuid} og ikke {id:str} (Codex P2), av samme grunn som
        # {id:int} på detaljrutene under: planfunksjonene tar UUID, så
        # `/v1/plan/not-a-uuid/aktiver` reiste `InvalidTextRepresentation`,
        # ble fanget som en generisk databasefeil og svarte 503
        # `db_utilgjengelig` — med en drifthendelse — på helt ordinær
        # klientinput. En ugyldig sti skal være 404 fra ROUTEREN, ikke en
        # kodevei og aller minst en falsk alarm.
        Route("/v1/plan/{id:uuid}/aktiver", pl_aktiver, methods=["POST"]),
        Route("/v1/plan/{id:uuid}/gjenoppta", pl_gjenoppta,
              methods=["POST"]),
        Route("/v1/plan/{id:uuid}/stans", pl_stans, methods=["POST"]),
        Route("/v1/plan/{id:uuid}/historikk", pl_historikk,
              methods=["GET"]),
        Route("/v1/domener", dm_utsted, methods=["POST"]),
        Route("/v1/modul/onboarding", mo_utsted, methods=["POST"]),
        Route("/v1/modul/onboarding/innlos", mo_innlos, methods=["POST"]),
        Route("/v1/modul/token/roter", mo_roter, methods=["POST"]),
        Route("/v1/modul/token/tilbakekall", mo_tilbakekall,
              methods=["POST"]),
        Route("/v1/oppdrag/kvittering", oppdrag_kvittering, methods=["POST"]),
        Route("/v1/oppdrag/forny", oppdrag_forny, methods=["POST"]),
        # #173: skriveveien inn i kandidatlagrene — claim-bundet, som
        # forny/kvittering.
        Route("/v1/rekruttering/kandidatdokument", kandidatdokument,
              methods=["POST"]),
        Route("/v1/rekruttering/kandidatartefakt", kandidatartefakt,
              methods=["POST"]),
        Route("/v1/artefakt", artefakt_upload, methods=["POST"]),
        # PR-008: rent lesende kundeflate. Merk rekkefølgen: den statiske
        # `/v1/policy/aktiv` registreres FØR mønsterruter kunne ha slukt
        # den, og detaljrutene bruker {id:int} så en ikke-numerisk sti er
        # 404 fra routeren, ikke en kodevei.
        Route("/v1/oversikt", oversikt, methods=["GET"]),
        Route("/v1/nokkeltall", nokkeltall, methods=["GET"]),
        Route("/v1/beslutninger", beslutninger, methods=["GET"]),
        Route("/v1/beslutninger/{id:int}", beslutning_detalj, methods=["GET"]),
        Route("/v1/rapport/{id:int}", rapport_detalj, methods=["GET"]),
        Route("/v1/rekruttering/rapport/{id:int}",
              rekrutteringsrapport_detalj, methods=["GET"]),
        Route("/v1/rekruttering/kandidatkort/{oppdrag_id:int}/{kandidat_id}",
              rekruttering_kandidatkort, methods=["GET"]),
        Route("/v1/rekruttering/kandidatdokument/{oppdrag_id:int}/{dokument_id}",
              rekruttering_kandidatdokument_les, methods=["GET"]),
        Route("/v1/rekruttering/evalueringer", rekrutteringsevalueringer,
              methods=["GET"]),
        Route("/v1/unntak/{id:int}", unntak_detalj, methods=["GET"]),
        Route("/v1/unntak/{id:int}/historikk", unntak_historikk,
              methods=["GET"]),
        Route("/v1/unntak/{id:int}/handling", unntak_handling,
              methods=["POST"]),
        Route("/v1/unntak/{id:int}/domeneattestasjon",
              unntak_domeneattestasjon, methods=["POST"]),
        Route("/v1/policy/aktiv", policy_aktiv, methods=["GET"]),
        Route("/v1/policy/{policy_id:str}/versjoner", ph_versjoner,
              methods=["GET"]),
        Route("/v1/policy/{policy_id:str}/diff", ph_diff, methods=["GET"]),
        Route("/v1/policyadmin/editorgrunnlag", ph_grunnlag,
              methods=["GET"]),
        # Lista over aktive policyer. Egen statisk sti, registrert
        # sammen med `aktiv` og FØR mønsterrutene: den er utveien når
        # `aktiv` (med rette) nekter å velge mellom flere.
        Route("/v1/policy/aktive", policy_aktive, methods=["GET"]),
        # Utrullingsplanen: øktbundet, fordi den ellers måtte ligge i den
        # statisk serverte klientbunten der hvem som helst kunne lese hver
        # tenants plan og modultildeling.
        Route("/v1/utrulling", utrulling, methods=["GET"]),
        Route("/v1/modellstyring", modellstyring, methods=["GET"]),
        Route("/v1/kontinuitet", kontinuitet, methods=["GET"]),
        Route("/v1/kontinuitet/hendelser", kontinuitet_hendelser,
              methods=["POST"]),
        Route("/v1/kontinuitet/hendelse/{hendelse_id:str}/post",
              kontinuitet_post, methods=["POST"]),
        Route("/v1/kontinuitet/hendelse/{hendelse_id:str}/lukk",
              kontinuitet_lukk, methods=["POST"]),
        Route("/v1/dokumentmal", dokumentmal, methods=["GET"]),
        Route("/v1/dokumentmal/familier", dokumentmal_familier,
              methods=["POST"]),
        Route("/v1/dokumentmal/versjoner", dokumentmal_versjoner,
              methods=["POST"]),
        Route("/v1/dokumentmal/versjon/{versjon_id:str}/publiser",
              dokumentmal_publiser, methods=["POST"]),
        Route("/v1/dokumentmal/versjon/{versjon_id:str}/trekk-tilbake",
              dokumentmal_trekk_tilbake, methods=["POST"]),
        Route("/v1/dokumentmal/versjon/{versjon_id:str}/utfylling",
              dokumentmal_utfylling, methods=["POST"]),
        # 096 (M-21): kolleksjonsrutene FØR mønsterrutene, som ellers i
        # fila — {plikt_id:uuid} avviser «lukk»/«bortfall» uansett, men
        # rekkefølgen sier intensjonen.
        Route("/v1/plikt", plikt_liste, methods=["GET"]),
        Route("/v1/plikt", plikt_registrer, methods=["POST"]),
        Route("/v1/plikt/{plikt_id:uuid}/lukk", plikt_lukk,
              methods=["POST"]),
        Route("/v1/plikt/{plikt_id:uuid}/bortfall", plikt_bortfall,
              methods=["POST"]),
        # 097 (M-12): kolleksjons- og handlingsrutene FØR mønsterruten,
        # som ellers i fila — {tilgang_id:uuid} avviser «objekt»
        # uansett, men rekkefølgen sier intensjonen.
        Route("/v1/tilgang", tilgang_liste, methods=["GET"]),
        Route("/v1/tilgang", tilgang_registrer, methods=["POST"]),
        Route("/v1/tilgang/objekt", tilgang_objekt, methods=["POST"]),
        Route("/v1/tilgang/{tilgang_id:uuid}/gjennomgang",
              tilgang_gjennomgang, methods=["POST"]),
        # 098 (M-22): samme rekkefølge og samme begrunnelse — kolleksjons-
        # rutene FØR mønsterrutene.
        Route("/v1/lisens", lisens_liste, methods=["GET"]),
        Route("/v1/lisens", lisens_registrer, methods=["POST"]),
        Route("/v1/lisens/{lisens_id:uuid}/fornyelse", lisens_fornyelse,
              methods=["POST"]),
        Route("/v1/lisens/{lisens_id:uuid}/avslutt", lisens_avslutt,
              methods=["POST"]),
        # 099 (M-30): kolleksjonsrutene FØR mønsterrutene, som ellers i
        # fila — {sak_id:uuid} avviser «svar»/«avvis»/«forleng» uansett,
        # men rekkefølgen sier intensjonen.
        Route("/v1/personvern", personvern_liste, methods=["GET"]),
        Route("/v1/personvern", personvern_registrer, methods=["POST"]),
        Route("/v1/personvern/{sak_id:uuid}/svar", personvern_svar,
              methods=["POST"]),
        Route("/v1/personvern/{sak_id:uuid}/avvis", personvern_avvis,
              methods=["POST"]),
        Route("/v1/personvern/{sak_id:uuid}/forleng", personvern_forleng,
              methods=["POST"]),
        # 100 (M-34): kolleksjonsruten FØR mønsterrutene, som ellers i
        # fila — {kontroll_id:uuid} avviser undernavnene uansett, men
        # rekkefølgen sier intensjonen.
        Route("/v1/compliance", compliance_bilde, methods=["GET"]),
        Route("/v1/compliance/kontroll", compliance_registrer,
              methods=["POST"]),
        Route("/v1/compliance/kontroll/{kontroll_id:uuid}/etterproving",
              compliance_etterproving, methods=["POST"]),
        Route("/v1/compliance/kontroll/{kontroll_id:uuid}/ikke-relevant",
              compliance_ikke_relevant, methods=["POST"]),
        # 101 (M-13): kolleksjonsruten FØRST, som ellers i fila. De fire
        # registreringsveiene er egne stier og ikke ett endepunkt med et
        # typefelt — en konto, en bankpost, et bilag og en match har
        # ulike kropper, ulike dommer og ulike feilmeldinger, og ett
        # endepunkt med fire former ville gjort hver av dem uleselig.
        Route("/v1/avstemming", avstemming_bilde, methods=["GET"]),
        Route("/v1/avstemming/konto", avstemming_konto, methods=["POST"]),
        Route("/v1/avstemming/bankpost", avstemming_bankpost,
              methods=["POST"]),
        Route("/v1/avstemming/bilag", avstemming_bilag, methods=["POST"]),
        Route("/v1/avstemming/match", avstemming_match, methods=["POST"]),
        Route("/v1/avstemming/match/{avstemming_id:uuid}/opphev",
              avstemming_opphev, methods=["POST"]),
        # 102 (M-17): kolleksjonsruten FØRST, som ellers i fila.
        Route("/v1/kundeservice", kundeservice_koe, methods=["GET"]),
        Route("/v1/kundeservice/henvendelse", kundeservice_ta_imot,
              methods=["POST"]),
        Route("/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/innhold",
              kundeservice_innhold, methods=["GET"]),
        Route("/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/utkast",
              kundeservice_utkastene, methods=["GET"]),
        # STIEN STÅR PÅ ÉN LINJE, og det er ikke stil: porten i
        # `test_pr008.py` parser KILDEN med et regex som ikke forstår
        # implisitt strengsammenslåing. En sti brutt over to linjer blir
        # en «død deklarasjon» i RUTESCOPE — altså en rute porten ikke
        # kan se at finnes. `/klassifiser` og ikke `/klassifisering` for
        # at linjen skal få plass.
        Route("/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/klassifiser",
              kundeservice_klassifiser, methods=["POST"]),
        Route("/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/unntakskoe",
              kundeservice_unntakskoe, methods=["POST"]),
        Route("/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/utkast/ny",
              kundeservice_utkast, methods=["POST"]),
        Route("/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/lukk",
              kundeservice_lukk, methods=["POST"]),
        Route("/v1/kundeservice/utkast/{utkast_id:uuid}/dom",
              kundeservice_utkastdom, methods=["POST"]),
        # 103 (M-18): kolleksjonsruten FØRST. STIENE STÅR PÅ ÉN LINJE —
        # porten i `test_pr008.py` parser kilden med et regex som ikke
        # forstår implisitt strengsammenslåing (102s lærdom).
        Route("/v1/onboarding", onboarding_bilde, methods=["GET"]),
        Route("/v1/onboarding/mal", onboarding_mal, methods=["POST"]),
        Route("/v1/onboarding/mal/{mal_id:uuid}/steg", onboarding_malsteg,
              methods=["POST"]),
        Route("/v1/onboarding/lop", onboarding_start, methods=["POST"]),
        Route("/v1/onboarding/lop/{lop_id:uuid}/steg", onboarding_stegene,
              methods=["GET"]),
        Route("/v1/onboarding/lop/{lop_id:uuid}/steg/{steg_nr:int}/eier",
              onboarding_stegeier, methods=["POST"]),
        Route("/v1/onboarding/lop/{lop_id:uuid}/steg/{steg_nr:int}/fullfor",
              onboarding_fullfor, methods=["POST"]),
        Route("/v1/onboarding/lop/{lop_id:uuid}/avslutt",
              onboarding_avslutt, methods=["POST"]),
        # 104 (M-23): kolleksjonsruten FØRST, og `purreplan` FØR
        # mønsterruten — {fordring_id:uuid} avviser ordet uansett, men
        # rekkefølgen sier intensjonen. STIENE STÅR PÅ ÉN LINJE (102s
        # lærdom: porten i test_pr008 parser kilden med regex).
        Route("/v1/fordring", fordring_bilde, methods=["GET"]),
        Route("/v1/fordring", fordring_registrer, methods=["POST"]),
        Route("/v1/fordring/purreplan", fordring_purreplan,
              methods=["POST"]),
        Route("/v1/fordring/{fordring_id:uuid}/hendelser",
              fordring_hendelser, methods=["GET"]),
        Route("/v1/fordring/{fordring_id:uuid}/betaling",
              fordring_betaling, methods=["POST"]),
        Route("/v1/fordring/{fordring_id:uuid}/neste-trinn",
              fordring_neste_trinn, methods=["POST"]),
        Route("/v1/fordring/{fordring_id:uuid}/ettergi", fordring_ettergi,
              methods=["POST"]),
        # 105 (M-24): kolleksjonsruten FØRST, og ORDRUTENE (`terskler`,
        # `part`, `avtale`) FØR mønsterruten — {avtale_id:uuid} avviser
        # ordene uansett, men rekkefølgen sier intensjonen. STIENE STÅR
        # PÅ ÉN LINJE (102s lærdom: porten i test_pr008 parser kilden
        # med regex).
        Route("/v1/leverandor", leverandor_bilde, methods=["GET"]),
        Route("/v1/leverandor/terskler", leverandor_terskler,
              methods=["POST"]),
        Route("/v1/leverandor/part", leverandor_part, methods=["POST"]),
        Route("/v1/leverandor/avtale", leverandor_avtale,
              methods=["POST"]),
        Route("/v1/leverandor/{avtale_id:uuid}/leveranser",
              leverandor_leveranser, methods=["GET"]),
        Route("/v1/leverandor/{avtale_id:uuid}/leveranse",
              leverandor_leveranse, methods=["POST"]),
        Route("/v1/leverandor/{avtale_id:uuid}/avslutt",
              leverandor_avslutt, methods=["POST"]),
        # 106 (M-14): kolleksjonsruten FØRST, og ORDRUTENE (`terskler`,
        # `mvasats`) FØR mønsterruten. STIENE STÅR PÅ ÉN LINJE (102s
        # lærdom: porten i test_pr008 parser kilden med regex).
        Route("/v1/faktura", faktura_bilde, methods=["GET"]),
        Route("/v1/faktura", faktura_registrer, methods=["POST"]),
        Route("/v1/faktura/terskler", faktura_terskler, methods=["POST"]),
        Route("/v1/faktura/mvasats", faktura_mvasats, methods=["POST"]),
        Route("/v1/faktura/{faktura_id:uuid}/kontroller",
              faktura_kontroller, methods=["GET"]),
        Route("/v1/faktura/{faktura_id:uuid}/kontroll", faktura_kontroll,
              methods=["POST"]),
        Route("/v1/faktura/{faktura_id:uuid}/avgjor", faktura_avgjor,
              methods=["POST"]),
        # 107 (M-25): kolleksjonsruten FØRST, og ORDRUTEN `terskler` FØR
        # mønsterruten. STIENE STÅR PÅ ÉN LINJE (102s lærdom).
        Route("/v1/prosjekt", prosjekt_bilde, methods=["GET"]),
        Route("/v1/prosjekt", prosjekt_registrer, methods=["POST"]),
        Route("/v1/prosjekt/terskler", prosjekt_terskler,
              methods=["POST"]),
        Route("/v1/prosjekt/{prosjekt_id:uuid}/milepaeler",
              prosjekt_milepaeler, methods=["GET"]),
        Route("/v1/prosjekt/{prosjekt_id:uuid}/arbeidsliste",
              prosjekt_arbeidsliste, methods=["GET"]),
        Route("/v1/prosjekt/{prosjekt_id:uuid}/betalingsplan",
              prosjekt_betalingsplan, methods=["POST"]),
        Route("/v1/prosjekt/{prosjekt_id:uuid}/milepael",
              prosjekt_milepael, methods=["POST"]),
        Route("/v1/prosjekt/{prosjekt_id:uuid}/arbeid", prosjekt_arbeid,
              methods=["POST"]),
        Route("/v1/prosjekt/{prosjekt_id:uuid}/avslutt", prosjekt_avslutt,
              methods=["POST"]),
        # 108 (M-26): kolleksjonsruten FØRST, og ORDRUTENE (`terskler`,
        # `produkt`, `klausul`) FØR mønsterruten. STIENE STÅR PÅ ÉN LINJE
        # (102s lærdom).
        Route("/v1/prisbok", prisbok_bilde, methods=["GET"]),
        Route("/v1/prisbok/terskler", prisbok_terskler, methods=["POST"]),
        Route("/v1/prisbok/produkt", prisbok_produkt, methods=["POST"]),
        Route("/v1/prisbok/klausul", prisbok_klausul, methods=["POST"]),
        Route("/v1/prisbok/{produkt_id:uuid}/historikk",
              prisbok_historikk, methods=["GET"]),
        Route("/v1/prisbok/{produkt_id:uuid}/paa-dato", prisbok_paa_dato,
              methods=["GET"]),
        Route("/v1/prisbok/{produkt_id:uuid}/pris", prisbok_pris,
              methods=["POST"]),
        Route("/v1/prisbok/{produkt_id:uuid}/aktiv", prisbok_aktiv,
              methods=["POST"]),
        # 109 (M-27): kolleksjonsruten FØRST, og ORDRUTENE (`terskler`,
        # `vare`) FØR mønsterrutene. STIENE STÅR PÅ ÉN LINJE (102s
        # lærdom).
        Route("/v1/lager", lager_bilde, methods=["GET"]),
        Route("/v1/lager/terskler", lager_terskler, methods=["POST"]),
        Route("/v1/lager/vare", lager_vare, methods=["POST"]),
        Route("/v1/lager/{vare_id:uuid}/bevegelser", lager_bevegelser,
              methods=["GET"]),
        Route("/v1/lager/{vare_id:uuid}/paa-dato", lager_paa_dato,
              methods=["GET"]),
        Route("/v1/lager/{vare_id:uuid}/punkt", lager_punkt,
              methods=["POST"]),
        Route("/v1/lager/{vare_id:uuid}/bevegelse", lager_bevegelse,
              methods=["POST"]),
        Route("/v1/lager/{vare_id:uuid}/telling", lager_telling,
              methods=["POST"]),
        Route("/v1/lager/{vare_id:uuid}/aktiv", lager_aktiv,
              methods=["POST"]),
        # 110 (M-42): kolleksjonsruten FØRST, og ORDRUTENE (`terskler`,
        # `mottaker`, `oppgave`) FØR mønsterrutene. STIENE STÅR PÅ ÉN
        # LINJE (102s lærdom).
        Route("/v1/kontovakt", kontovakt_bilde, methods=["GET"]),
        Route("/v1/kontovakt/terskler", kontovakt_terskler,
              methods=["POST"]),
        Route("/v1/kontovakt/mottaker", kontovakt_mottaker,
              methods=["POST"]),
        Route("/v1/kontovakt/oppgave/{oppgave_id:uuid}/verifikasjon",
              kontovakt_verifikasjon, methods=["POST"]),
        Route("/v1/kontovakt/{mottaker_id:uuid}/historikk",
              kontovakt_historikk, methods=["GET"]),
        Route("/v1/kontovakt/{mottaker_id:uuid}/konto", kontovakt_konto,
              methods=["POST"]),
        Route("/v1/kontovakt/{mottaker_id:uuid}/aktiv", kontovakt_aktiv,
              methods=["POST"]),
        # 111 (M-41): kolleksjonsruten FØRST, og ORDRUTENE (`terskler`,
        # `subjekt`) FØR mønsterrutene. STIENE STÅR PÅ ÉN LINJE.
        Route("/v1/betaling", betaling_bilde, methods=["GET"]),
        Route("/v1/betaling/terskler", betaling_terskler,
              methods=["POST"]),
        Route("/v1/betaling/subjekt", betaling_subjekt,
              methods=["POST"]),
        Route("/v1/betaling/{subjekt_id:uuid}/historikk",
              betaling_historikk, methods=["GET"]),
        Route("/v1/betaling/{subjekt_id:uuid}/status", betaling_status,
              methods=["POST"]),
        Route("/v1/betaling/{subjekt_id:uuid}/abonnement",
              betaling_abonnement, methods=["POST"]),
        Route("/v1/betaling/{subjekt_id:uuid}/aktiv", betaling_aktiv,
              methods=["POST"]),
        Route("/v1/tilskudd", tilskudd_bilde, methods=["GET"]),
        Route("/v1/tilskudd/funn", tilskudd_funn, methods=["GET"]),
        Route("/v1/tilskudd/kildeposter", tilskudd_kildeposter,
              methods=["GET"]),
        Route("/v1/tilskudd/krav", tilskudd_krav, methods=["POST"]),
        Route("/v1/tilskudd/ordning", tilskudd_ordning,
              methods=["POST"]),
        Route("/v1/tilskudd/kildepost", tilskudd_kildepost,
              methods=["POST"]),
        Route("/v1/tilskudd/estimat/{estimat_id:uuid}/poster",
              tilskudd_poster, methods=["GET"]),
        Route("/v1/tilskudd/estimat/{estimat_id:uuid}/forutsetninger",
              tilskudd_forutsetninger, methods=["GET"]),
        Route("/v1/tilskudd/estimat/{estimat_id:uuid}/post",
              tilskudd_post, methods=["POST"]),
        Route("/v1/tilskudd/estimat/{estimat_id:uuid}/forutsetning",
              tilskudd_forutsetning, methods=["POST"]),
        Route("/v1/tilskudd/estimat/{estimat_id:uuid}/ferdigstill",
              tilskudd_ferdigstill, methods=["POST"]),
        Route("/v1/tilskudd/{ordning_id:uuid}/estimater",
              tilskudd_estimater, methods=["GET"]),
        Route("/v1/tilskudd/{ordning_id:uuid}/estimat",
              tilskudd_estimat_ny, methods=["POST"]),
        Route("/v1/tilskudd/{ordning_id:uuid}/aktiv", tilskudd_aktiv,
              methods=["POST"]),
        Route("/v1/tilskudd/{ordning_id:uuid}/funn/lukk",
              tilskudd_lukk_funn, methods=["POST"]),
        # M-52 (122). Faste stier FØR parametriserte, ellers ville
        # `/v1/toll/vare` blitt lest som en id.
        # M-50 (124). Faste stier FØR parametriserte.
        Route("/v1/journal", journ_bilde, methods=["GET"]),
        Route("/v1/journal/kilder", journ_kilder, methods=["GET"]),
        Route("/v1/journal/poster", journ_poster, methods=["GET"]),
        Route("/v1/journal/funn", journ_funn, methods=["GET"]),
        Route("/v1/journal/krav", journ_krav, methods=["POST"]),
        Route("/v1/journal/kilde", journ_kilde_ny, methods=["POST"]),
        Route("/v1/journal/sak", journ_sak, methods=["POST"]),
        Route("/v1/journal/post", journ_post_ny, methods=["POST"]),
        Route("/v1/journal/post/{post_id:uuid}/personer",
              journ_personer, methods=["GET"]),
        Route("/v1/journal/kilde/{kilde_id:uuid}/gyldig-til",
              journ_gyldig_til, methods=["POST"]),
        Route("/v1/journal/person/{person_id:uuid}/anonymiser",
              journ_anonymiser, methods=["POST"]),
        Route("/v1/journal/funn/{funn_id:uuid}/lukk",
              journ_lukk_funn, methods=["POST"]),
        # M-15 (128). Faste stier FØR parametriserte.
        Route("/v1/likviditet", likv_bilde, methods=["GET"]),
        Route("/v1/likviditet/prognoser", likv_prognoser,
              methods=["GET"]),
        Route("/v1/likviditet/poster", likv_poster, methods=["GET"]),
        Route("/v1/likviditet/modeller", likv_modeller,
              methods=["GET"]),
        Route("/v1/likviditet/tiltak", likv_tiltak, methods=["GET"]),
        Route("/v1/likviditet/funn", likv_funn, methods=["GET"]),
        Route("/v1/likviditet/krav", likv_krav, methods=["POST"]),
        Route("/v1/likviditet/modell", likv_modell_ny,
              methods=["POST"]),
        Route("/v1/likviditet/post", likv_post_ny, methods=["POST"]),
        Route("/v1/likviditet/prognose", likv_prognose_ny,
              methods=["POST"]),
        Route("/v1/likviditet/tiltak", likv_tiltak_ny,
              methods=["POST"]),
        Route("/v1/likviditet/prognose/{prognose_id:uuid}/bane",
              likv_bane, methods=["GET"]),
        Route("/v1/likviditet/prognose/{prognose_id:uuid}/maaling",
              likv_maaling, methods=["POST"]),
        Route("/v1/likviditet/tiltak/{tiltak_id:uuid}/vurder",
              likv_vurder, methods=["POST"]),
        Route("/v1/likviditet/funn/{funn_id:uuid}/lukk",
              likv_lukk_funn, methods=["POST"]),
        # M-33 (130). Faste stier FØR parametriserte.
        Route("/v1/prognose", prog_bilde, methods=["GET"]),
        Route("/v1/prognose/prognoser", prog_prognoser,
              methods=["GET"]),
        Route("/v1/prognose/modeller", prog_modeller,
              methods=["GET"]),
        Route("/v1/prognose/funn", prog_funn, methods=["GET"]),
        Route("/v1/prognose/krav", prog_krav, methods=["POST"]),
        Route("/v1/prognose/modell", prog_modell_ny,
              methods=["POST"]),
        Route("/v1/prognose/prognose", prog_prognose_ny,
              methods=["POST"]),
        Route("/v1/prognose/modell/{modell_id:uuid}/avvikle",
              prog_modell_avvikle, methods=["POST"]),
        Route("/v1/prognose/prognose/{prognose_id:uuid}/bane",
              prog_bane, methods=["GET"]),
        Route("/v1/prognose/prognose/{prognose_id:uuid}/maaling",
              prog_maaling, methods=["POST"]),
        Route("/v1/prognose/funn/{funn_id:uuid}/lukk",
              prog_lukk_funn, methods=["POST"]),
        # M-36 (132). Faste stier FØR parametriserte.
        Route("/v1/optimalisator", opti_bilde, methods=["GET"]),
        Route("/v1/optimalisator/rangeringer", opti_rangeringer,
              methods=["GET"]),
        Route("/v1/optimalisator/tiltak", opti_tiltak,
              methods=["GET"]),
        Route("/v1/optimalisator/modeller", opti_modeller,
              methods=["GET"]),
        Route("/v1/optimalisator/funn", opti_funn, methods=["GET"]),
        Route("/v1/optimalisator/signaler", opti_signaler,
              methods=["GET"]),
        Route("/v1/optimalisator/krav", opti_krav, methods=["POST"]),
        Route("/v1/optimalisator/modell", opti_modell_ny,
              methods=["POST"]),
        Route("/v1/optimalisator/tiltak", opti_tiltak_ny,
              methods=["POST"]),
        Route("/v1/optimalisator/stopp", opti_stopp_ny,
              methods=["POST"]),
        Route("/v1/optimalisator/rangering", opti_rangere,
              methods=["POST"]),
        Route("/v1/optimalisator/modell/{modell_id:uuid}/avvikle",
              opti_modell_avvikle, methods=["POST"]),
        Route("/v1/optimalisator/tiltak/{tiltak_id:uuid}/vurder",
              opti_vurder, methods=["POST"]),
        Route("/v1/optimalisator/stopp/{stopp_id:uuid}/opphev",
              opti_stopp_opphev, methods=["POST"]),
        Route("/v1/optimalisator/rangering/{rangering_id:uuid}",
              opti_rangering, methods=["GET"]),
        Route("/v1/optimalisator/rangering/{rangering_id:uuid}/effekt",
              opti_effekt, methods=["POST"]),
        Route("/v1/optimalisator/funn/{funn_id:uuid}/lukk",
              opti_lukk_funn, methods=["POST"]),
        # M-7 (133). Faste stier FØR parametriserte.
        Route("/v1/mote", mote_bilde, methods=["GET"]),
        Route("/v1/mote/moter", mote_moter, methods=["GET"]),
        Route("/v1/mote/hjemler", mote_hjemler, methods=["GET"]),
        Route("/v1/mote/aksjoner", mote_aksjoner, methods=["GET"]),
        Route("/v1/mote/funn", mote_funn, methods=["GET"]),
        Route("/v1/mote/krav", mote_krav, methods=["POST"]),
        Route("/v1/mote/hjemmel", mote_hjemmel_ny, methods=["POST"]),
        Route("/v1/mote/mote", mote_mote_ny, methods=["POST"]),
        Route("/v1/mote/hjemmel/{hjemmel_id:uuid}/avslutt",
              mote_hjemmel_avslutt, methods=["POST"]),
        Route("/v1/mote/aksjon/{aksjon_id:uuid}/lukk",
              mote_aksjon_lukk, methods=["POST"]),
        Route("/v1/mote/funn/{funn_id:uuid}/lukk",
              mote_lukk_funn, methods=["POST"]),
        Route("/v1/mote/{mote_id:uuid}/referat", mote_referat,
              methods=["GET"]),
        Route("/v1/mote/{mote_id:uuid}/opptak", mote_opptak,
              methods=["POST"]),
        Route("/v1/mote/{mote_id:uuid}/referatpunkt", mote_punkt,
              methods=["POST"]),
        Route("/v1/mote/{mote_id:uuid}/beslutning", mote_beslutning,
              methods=["POST"]),
        Route("/v1/mote/{mote_id:uuid}/aksjon", mote_aksjon_ny,
              methods=["POST"]),
        Route("/v1/innhold", innhold_bilde, methods=["GET"]),
        Route("/v1/innhold/sider", innhold_sider, methods=["GET"]),
        Route("/v1/innhold/kilder", innhold_kilder, methods=["GET"]),
        Route("/v1/innhold/funn", innhold_funn, methods=["GET"]),
        Route("/v1/innhold/krav", innhold_krav, methods=["POST"]),
        Route("/v1/innhold/kilde", innhold_kilde_ny, methods=["POST"]),
        Route("/v1/innhold/utkast", innhold_utkast_ny, methods=["POST"]),
        Route("/v1/innhold/utkast/{utkast_id:uuid}", innhold_utkastet,
              methods=["GET"]),
        Route("/v1/innhold/utkast/{utkast_id:uuid}/paastand",
              innhold_paastand, methods=["POST"]),
        Route("/v1/innhold/utkast/{utkast_id:uuid}/visning",
              innhold_visning, methods=["POST"]),
        Route("/v1/innhold/utkast/{utkast_id:uuid}/klar",
              innhold_klar, methods=["POST"]),
        Route("/v1/innhold/utkast/{utkast_id:uuid}/publiser",
              innhold_publiser, methods=["POST"]),
        Route("/v1/innhold/publisering/{publisering_id:uuid}/tilbake",
              innhold_tilbake, methods=["POST"]),
        Route("/v1/innhold/funn/{funn_id:uuid}/lukk",
              innhold_lukk_funn, methods=["POST"]),
        Route("/v1/telefoni", telefoni_bilde, methods=["GET"]),
        Route("/v1/telefoni/samtaler", telefoni_samtaler,
              methods=["GET"]),
        Route("/v1/telefoni/hjemler", telefoni_hjemler, methods=["GET"]),
        Route("/v1/telefoni/regler", telefoni_regler, methods=["GET"]),
        Route("/v1/telefoni/funn", telefoni_funn, methods=["GET"]),
        Route("/v1/telefoni/krav", telefoni_krav, methods=["POST"]),
        Route("/v1/telefoni/hjemmel", telefoni_hjemmel_ny,
              methods=["POST"]),
        Route("/v1/telefoni/regel", telefoni_regel_ny, methods=["POST"]),
        Route("/v1/telefoni/regel/{regel_id:uuid}/avvikle",
              telefoni_regel_avvikle, methods=["POST"]),
        Route("/v1/telefoni/samtale", telefoni_samtale_ny,
              methods=["POST"]),
        Route("/v1/telefoni/samtale/{samtale_id:uuid}/transkripsjon",
              telefoni_transkripsjon, methods=["GET"]),
        Route("/v1/telefoni/samtale/{samtale_id:uuid}/avslutt",
              telefoni_samtale_avslutt, methods=["POST"]),
        Route("/v1/telefoni/samtale/{samtale_id:uuid}/opptak",
              telefoni_opptak, methods=["POST"]),
        Route("/v1/telefoni/samtale/{samtale_id:uuid}/linje",
              telefoni_linje, methods=["POST"]),
        Route("/v1/telefoni/samtale/{samtale_id:uuid}/eskaler",
              telefoni_eskaler, methods=["POST"]),
        Route("/v1/telefoni/eskalering/{eskalering_id:uuid}/lukk",
              telefoni_eskalering_lukk, methods=["POST"]),
        Route("/v1/telefoni/funn/{funn_id:uuid}/lukk",
              telefoni_lukk_funn, methods=["POST"]),
        Route("/v1/esg", esg_bilde, methods=["GET"]),
        Route("/v1/esg/perioder", esg_perioder, methods=["GET"]),
        Route("/v1/esg/faktorer", esg_faktorer, methods=["GET"]),
        Route("/v1/esg/funn", esg_funn, methods=["GET"]),
        Route("/v1/esg/krav", esg_krav, methods=["POST"]),
        Route("/v1/esg/kilde", esg_kilde_ny, methods=["POST"]),
        Route("/v1/esg/periode", esg_periode_ny, methods=["POST"]),
        Route("/v1/esg/faktor", esg_faktor_ny, methods=["POST"]),
        Route("/v1/esg/faktor/{faktor_id:uuid}/avvikle",
              esg_faktor_avvikle, methods=["POST"]),
        Route("/v1/esg/periode/{periode_id:uuid}/maalinger",
              esg_maalinger, methods=["GET"]),
        Route("/v1/esg/periode/{periode_id:uuid}/paastander",
              esg_paastander, methods=["GET"]),
        Route("/v1/esg/periode/{periode_id:uuid}/lukk",
              esg_periode_lukk, methods=["POST"]),
        Route("/v1/esg/periode/{periode_id:uuid}/maaling",
              esg_maaling_ny, methods=["POST"]),
        Route("/v1/esg/periode/{periode_id:uuid}/paastand",
              esg_paastand_ny, methods=["POST"]),
        Route("/v1/esg/periode/{periode_id:uuid}/sammenstill",
              esg_sammenstill, methods=["POST"]),
        Route("/v1/esg/funn/{funn_id:uuid}/lukk", esg_lukk_funn,
              methods=["POST"]),
        # M-53 (127). Faste stier FØR parametriserte.
        Route("/v1/hms", hms_bilde, methods=["GET"]),
        Route("/v1/hms/avvik", hms_avvik, methods=["GET"]),
        Route("/v1/hms/regelverk", hms_regelverk, methods=["GET"]),
        Route("/v1/hms/funn", hms_funn, methods=["GET"]),
        Route("/v1/hms/krav", hms_krav, methods=["POST"]),
        Route("/v1/hms/regelverk", hms_regel_ny, methods=["POST"]),
        Route("/v1/hms/avvik", hms_meld, methods=["POST"]),
        Route("/v1/hms/avvik/{avvik_id:uuid}/tiltak", hms_tiltak,
              methods=["GET"]),
        Route("/v1/hms/avvik/{avvik_id:uuid}/oppbevaringsgrunnlag",
              hms_grunnlag, methods=["GET"]),
        Route("/v1/hms/avvik/{avvik_id:uuid}/tiltak", hms_tiltak_ny,
              methods=["POST"]),
        Route("/v1/hms/avvik/{avvik_id:uuid}/anonymiser",
              hms_anonymiser, methods=["POST"]),
        Route("/v1/hms/funn/{funn_id:uuid}/lukk", hms_lukk_funn,
              methods=["POST"]),
        # M-47 (123). Faste stier FØR parametriserte, ellers ville
        # `/v1/myndighet/plikttype` blitt lest som en id.
        Route("/v1/myndighet", mynd_bilde, methods=["GET"]),
        Route("/v1/myndighet/regelverk", mynd_regelverk_les,
              methods=["GET"]),
        Route("/v1/myndighet/plikter", mynd_plikter_les,
              methods=["GET"]),
        Route("/v1/myndighet/funn", mynd_funn, methods=["GET"]),
        Route("/v1/myndighet/krav", mynd_krav, methods=["POST"]),
        Route("/v1/myndighet/regelverk", mynd_regelverk_ny,
              methods=["POST"]),
        Route("/v1/myndighet/plikttype", mynd_plikttype,
              methods=["POST"]),
        Route("/v1/myndighet/plikt", mynd_plikt_ny, methods=["POST"]),
        Route("/v1/myndighet/regelverk/{regelverk_id:uuid}/gyldig-til",
              mynd_gyldig_til, methods=["POST"]),
        Route("/v1/myndighet/plikt/{plikt_id:uuid}/bevis", mynd_bevis,
              methods=["POST"]),
        Route("/v1/myndighet/funn/{funn_id:uuid}/lukk",
              mynd_lukk_funn, methods=["POST"]),
        Route("/v1/toll", toll_bilde, methods=["GET"]),
        Route("/v1/toll/funn", toll_funn, methods=["GET"]),
        Route("/v1/toll/krav", toll_krav, methods=["POST"]),
        Route("/v1/toll/nomenklatur", toll_nomenklatur,
              methods=["POST"]),
        Route("/v1/toll/varenummer", toll_varenummer_ny,
              methods=["POST"]),
        Route("/v1/toll/vare", toll_vare, methods=["POST"]),
        Route("/v1/toll/nomenklatur/{nomenklatur_id:uuid}/varenummer",
              toll_varenummer_les, methods=["GET"]),
        Route("/v1/toll/nomenklatur/{nomenklatur_id:uuid}/gyldig-til",
              toll_gyldig_til, methods=["POST"]),
        Route("/v1/toll/forslag/{forslag_id:uuid}/grunner",
              toll_grunner, methods=["GET"]),
        Route("/v1/toll/forslag/{forslag_id:uuid}/klart", toll_klart,
              methods=["POST"]),
        Route("/v1/toll/vare/{vare_id:uuid}/forslag",
              toll_forslag_les, methods=["GET"]),
        Route("/v1/toll/vare/{vare_id:uuid}/forslag",
              toll_forslag_ny, methods=["POST"]),
        Route("/v1/toll/funn/{funn_id:uuid}/lukk", toll_lukk_funn,
              methods=["POST"]),
        # M-54 (121). Samme rekkefølgeregel som M-55 under: faste
        # stier FØR parametriserte, ellers ville `/v1/ehf/regel`
        # blitt lest som en id.
        Route("/v1/ehf", ehf_bilde, methods=["GET"]),
        Route("/v1/ehf/funn", ehf_funn, methods=["GET"]),
        Route("/v1/ehf/krav", ehf_krav, methods=["POST"]),
        Route("/v1/ehf/regelsett", ehf_regelsett, methods=["POST"]),
        Route("/v1/ehf/regel", ehf_regel, methods=["POST"]),
        Route("/v1/ehf/dokument", ehf_dokument, methods=["POST"]),
        Route("/v1/ehf/regelsett/{regelsett_id:uuid}/regler",
              ehf_regler, methods=["GET"]),
        Route("/v1/ehf/regelsett/{regelsett_id:uuid}/gyldig-til",
              ehf_gyldig_til, methods=["POST"]),
        Route("/v1/ehf/validering/{validering_id:uuid}/avvik",
              ehf_avvik, methods=["GET"]),
        Route("/v1/ehf/dokument/{dokument_id:uuid}/valideringer",
              ehf_valideringer, methods=["GET"]),
        Route("/v1/ehf/dokument/{dokument_id:uuid}/felter",
              ehf_felter, methods=["POST"]),
        Route("/v1/ehf/dokument/{dokument_id:uuid}/valider",
              ehf_valider, methods=["POST"]),
        Route("/v1/ehf/avvik/{avvik_id:uuid}/retting", ehf_retting,
              methods=["POST"]),
        Route("/v1/ehf/retting/{retting_id:uuid}/klar", ehf_klar,
              methods=["POST"]),
        Route("/v1/ehf/funn/{funn_id:uuid}/lukk", ehf_lukk_funn,
              methods=["POST"]),
        # M-55 (120). REKKEFØLGEN ER IKKE VILKÅRLIG: de faste
        # stiene står FØR `{merkevare_id:uuid}`, ellers ville
        # `/v1/merkevare/funn` blitt lest som en merkevare-id.
        Route("/v1/merkevare", merkevare_bilde, methods=["GET"]),
        Route("/v1/merkevare/funn", merkevare_alle_funn,
              methods=["GET"]),
        Route("/v1/merkevare/bevaringskopier", merkevare_kopier,
              methods=["GET"]),
        Route("/v1/merkevare/varsler", merkevare_varsler,
              methods=["GET"]),
        Route("/v1/merkevare/funn/{funn_id:uuid}/vurderinger",
              merkevare_vurderinger, methods=["GET"]),
        Route("/v1/merkevare/krav", merkevare_krav, methods=["POST"]),
        Route("/v1/merkevare/merke", merkevare_merke,
              methods=["POST"]),
        Route("/v1/merkevare/bevaringskopi", merkevare_kopi,
              methods=["POST"]),
        Route("/v1/merkevare/funn", merkevare_funn_ny,
              methods=["POST"]),
        Route("/v1/merkevare/funn/{funn_id:uuid}/vurder",
              merkevare_vurder, methods=["POST"]),
        Route("/v1/merkevare/funn/{funn_id:uuid}/henvis",
              merkevare_henvis, methods=["POST"]),
        Route("/v1/merkevare/funn/{funn_id:uuid}/lukk",
              merkevare_lukk_funn, methods=["POST"]),
        Route("/v1/merkevare/varsel/{varsel_id:uuid}/lukk",
              merkevare_lukk_varsel, methods=["POST"]),
        Route("/v1/merkevare/{merkevare_id:uuid}/funn",
              merkevare_funn_for_merke, methods=["GET"]),
        Route("/v1/merkevare/{merkevare_id:uuid}/aktiv",
              merkevare_aktiv, methods=["POST"]),
        Route("/v1/anbud", anbud_bilde, methods=["GET"]),
        Route("/v1/anbud/funn", anbud_funn, methods=["GET"]),
        Route("/v1/anbud/kilder", anbud_kilder, methods=["GET"]),
        Route("/v1/anbud/profil", anbud_profil, methods=["POST"]),
        Route("/v1/anbud/registrer", anbud_registrer,
              methods=["POST"]),
        Route("/v1/anbud/kilde", anbud_kilde_ny, methods=["POST"]),
        Route("/v1/anbud/utkast/{utkast_id:uuid}/punkt", anbud_punkt,
              methods=["POST"]),
        Route("/v1/anbud/utkast/{utkast_id:uuid}/klart", anbud_klart,
              methods=["POST"]),
        Route("/v1/anbud/{anbud_id:uuid}/krav", anbud_krav_les,
              methods=["GET"]),
        Route("/v1/anbud/{anbud_id:uuid}/utkast", anbud_utkast_les,
              methods=["GET"]),
        Route("/v1/anbud/{anbud_id:uuid}/krav/ny", anbud_krav_ny,
              methods=["POST"]),
        Route("/v1/anbud/{anbud_id:uuid}/utkast/ny", anbud_utkast_ny,
              methods=["POST"]),
        Route("/v1/anbud/{anbud_id:uuid}/aktiv", anbud_aktiv,
              methods=["POST"]),
        Route("/v1/anbud/{anbud_id:uuid}/funn/lukk", anbud_lukk_funn,
              methods=["POST"]),
        Route("/v1/sanksjon", sanksjon_bilde, methods=["GET"]),
        Route("/v1/sanksjon/funn", sanksjon_funn, methods=["GET"]),
        Route("/v1/sanksjon/lister", sanksjon_lister,
              methods=["GET"]),
        Route("/v1/sanksjon/krav", sanksjon_krav, methods=["POST"]),
        Route("/v1/sanksjon/liste", sanksjon_liste, methods=["POST"]),
        Route("/v1/sanksjon/subjekt", sanksjon_subjekt,
              methods=["POST"]),
        Route("/v1/sanksjon/treff/{treff_id:uuid}/avklaring",
              sanksjon_avklaring, methods=["POST"]),
        Route("/v1/sanksjon/{subjekt_id:uuid}/kontroller",
              sanksjon_kontroller, methods=["GET"]),
        Route("/v1/sanksjon/{subjekt_id:uuid}/treff", sanksjon_treff,
              methods=["GET"]),
        Route("/v1/sanksjon/{subjekt_id:uuid}/kontroll",
              sanksjon_kontroll, methods=["POST"]),
        Route("/v1/sanksjon/{subjekt_id:uuid}/aktiv", sanksjon_aktiv,
              methods=["POST"]),
        Route("/v1/sanksjon/{subjekt_id:uuid}/funn/lukk",
              sanksjon_lukk_funn, methods=["POST"]),
        Route("/v1/motpart", motpart_bilde, methods=["GET"]),
        Route("/v1/motpart/funn", motpart_funn, methods=["GET"]),
        Route("/v1/motpart/krav", motpart_krav, methods=["POST"]),
        Route("/v1/motpart/registrer", motpart_registrer,
              methods=["POST"]),
        Route("/v1/motpart/versjon/{versjon_id:uuid}/vurdering",
              motpart_vurdering, methods=["POST"]),
        Route("/v1/motpart/{motpart_id:uuid}/historikk",
              motpart_historikk, methods=["GET"]),
        Route("/v1/motpart/{motpart_id:uuid}/oppslagslogg",
              motpart_oppslagslogg, methods=["GET"]),
        Route("/v1/motpart/{motpart_id:uuid}/oppslag", motpart_oppslag,
              methods=["POST"]),
        Route("/v1/motpart/{motpart_id:uuid}/deaktiver",
              motpart_deaktiver, methods=["POST"]),
        Route("/v1/motpart/{motpart_id:uuid}/funn/lukk",
              motpart_lukk_funn, methods=["POST"]),
        Route("/v1/adresse", adresse_bilde, methods=["GET"]),
        Route("/v1/adresse/krav", adresse_krav, methods=["POST"]),
        Route("/v1/adresse/subjekt", adresse_subjekt,
              methods=["POST"]),
        Route("/v1/adresse/{subjekt_id:uuid}/historikk",
              adresse_historikk, methods=["GET"]),
        Route("/v1/adresse/{subjekt_id:uuid}/versjon", adresse_versjon,
              methods=["POST"]),
        Route("/v1/adresse/{subjekt_id:uuid}/aktiv", adresse_aktiv,
              methods=["POST"]),
        Route("/v1/adresse/versjon/{versjon_id:uuid}/kontroller",
              adresse_kontroller, methods=["GET"]),
        Route("/v1/adresse/versjon/{versjon_id:uuid}/kontroll",
              adresse_kontroll, methods=["POST"]),
        Route("/v1/lonn", lonn_bilde, methods=["GET"]),
        Route("/v1/lonn/terskler", lonn_terskler, methods=["POST"]),
        Route("/v1/lonn/taker", lonn_taker, methods=["POST"]),
        Route("/v1/lonn/{taker_id:uuid}/dager", lonn_dager,
              methods=["GET"]),
        Route("/v1/lonn/{taker_id:uuid}/historikk", lonn_historikk,
              methods=["GET"]),
        Route("/v1/lonn/{taker_id:uuid}/planer", lonn_planer,
              methods=["GET"]),
        Route("/v1/lonn/{taker_id:uuid}/plan", lonn_plan,
              methods=["POST"]),
        Route("/v1/lonn/{taker_id:uuid}/timer", lonn_timer,
              methods=["POST"]),
        Route("/v1/lonn/{taker_id:uuid}/aktiv", lonn_aktiv,
              methods=["POST"]),
        Route("/v1/kampanje", kampanje_bilde, methods=["GET"]),
        Route("/v1/kampanje/grense", kampanje_grense,
              methods=["POST"]),
        Route("/v1/kampanje/mottaker", kampanje_mottaker,
              methods=["POST"]),
        Route("/v1/kampanje/kampanje", kampanje_ny, methods=["POST"]),
        Route("/v1/kampanje/mottaker/{mottaker_id:uuid}/samtykke",
              kampanje_historikk, methods=["GET"]),
        Route("/v1/kampanje/mottaker/{mottaker_id:uuid}/samtykke",
              kampanje_samtykke, methods=["POST"]),
        Route("/v1/kampanje/mottaker/{mottaker_id:uuid}/samtykke/{dag:str}",
              kampanje_samtykke_dato, methods=["GET"]),
        Route("/v1/kampanje/mottaker/{mottaker_id:uuid}/aktiv",
              kampanje_aktiv, methods=["POST"]),
        Route("/v1/kampanje/kampanje/{kampanje_id:uuid}/avlys",
              kampanje_avlys, methods=["POST"]),
        Route("/v1/kampanje/kampanje/{kampanje_id:uuid}/plan",
              kampanje_plan, methods=["POST"]),
        Route("/v1/drift/backup", drift_backup, methods=["GET"]),
        Route("/v1/drift/selvtest", drift_selvtest, methods=["GET"]),
        Route("/v1/datakvalitet", datakvalitet, methods=["GET"]),
        # M-4 (093): retensjonsregnskapet. Kontrollplanet er
        # `platform:admin` og avgjøres INNE i endepunktet
        # (`/v1/utrulling`-presedensen) — se RUTESCOPE-raden.
        Route("/v1/retensjon", retensjon, methods=["GET"]),
        Route("/v1/kunnskap", kunnskap, methods=["GET"]),
        Route("/v1/inndata/hent-for-oppdrag/{oppdrag_id:int}",
              inndata_hent, methods=["POST"]),
        Route("/v1/inndata/reserver", inndata_reserver,
              methods=["POST"]),
        Route("/v1/inndata/opplast/{jti:str}", inndata_opplast,
              methods=["PUT"]),
        Route("/v1/rekruttering/prosesser", rekruttering_prosesser,
              methods=["GET"]),
        Route("/v1/rekruttering/stillingsprofiler", rekruttering_profiler,
              methods=["GET"]),
        Route("/v1/rekruttering/stillingsprofiler",
              rekruttering_profil_lagre, methods=["POST"]),
        Route("/v1/rekruttering/stillingsprofil/{profil_id}/slett",
              rekruttering_profil_slett, methods=["POST"]),
        Route("/v1/rekruttering/utsendingstekster", rekruttering_tekster,
              methods=["GET"]),
        Route("/v1/rekruttering/utsendingstekster",
              rekruttering_tekst_lagre, methods=["POST"]),
        Route("/v1/rekruttering/utsendingstekst/{tekst_id}/slett",
              rekruttering_tekst_slett, methods=["POST"]),
        Route("/v1/rekruttering/evaluering/{oppdrag_id:int}/slett",
              rk_slett, methods=["POST"]),
        Route("/v1/rekruttering/evaluering/{oppdrag_id:int}/avbryt",
              rk_avbryt, methods=["POST"]),
        Route("/v1/rekruttering/prosesser/{prosess_id}/blinding",
              rekruttering_blinding, methods=["POST"]),
        Route("/v1/rekruttering/lister",
              rekruttering_liste_opprett, methods=["POST"]),
        Route("/v1/rekruttering/lister/{liste_id:uuid}/signer",
              rekruttering_signer, methods=["POST"]),
        Route("/v1/rekruttering/tidsvalg", rekruttering_tidsvalg,
              methods=["GET"]),
        Route("/v1/rekruttering/tidsvalg/slots", rekruttering_tidsvalg_slots,
              methods=["POST"]),
        Route("/v1/rekruttering/tidsvalg/slot/{slot_id}/deaktiver",
              rekruttering_tidsvalg_deaktiver, methods=["POST"]),
        # M-6 PR-B: de statiske kilderutene FØR mønsterruten, så
        # {kilde_id:uuid} aldri slukter "start"/"callback" (uuid-
        # konverteren avviser dem uansett — rekkefølgen sier intensjonen).
        Route("/v1/epost/kilder", epost_kilder, methods=["GET"]),
        Route("/v1/epost/kilder/start", epost_kilde_start,
              methods=["POST"]),
        Route("/v1/epost/kilder/callback", epost_kilde_callback,
              methods=["GET"]),
        Route("/v1/epost/kilder/{kilde_id:uuid}/deaktiver",
              epost_kilde_deaktiver, methods=["POST"]),
        Route("/v1/tidsvalg/oppslag", tidsvalg_oppslag, methods=["POST"]),
        Route("/v1/tidsvalg/velg", tidsvalg_velg, methods=["POST"]),
        Route("/tidsvalg", tidsvalg_side, methods=["GET"]),
        # PR-013: policyadministrasjon. Kolleksjonsrutene FØR mønsterrutene, og
        # de spesifikke handlings-subrutene (.../valider osv.) er egne stier så
        # {utkast_id:str} aldri slukter dem.
        Route("/v1/policymaler", pa_maler, methods=["GET"]),
        Route("/v1/policyutkast", pa_opprett_utkast, methods=["POST"]),
        Route("/v1/policyutkast", pa_list_utkast, methods=["GET"]),
        Route("/v1/policyutkast/{utkast_id:str}/simuler", pa_simuler_utkast,
              methods=["POST"]),
        Route("/v1/policyutkast/{utkast_id:str}/valider", pa_valider_utkast,
              methods=["POST"]),
        Route("/v1/varsel", pa_varsel_liste, methods=["GET"]),
        Route("/v1/varsel/{varsel_id:str}/lest", pa_varsel_lest,
              methods=["POST"]),
        Route("/v1/varselvalg", pa_varselvalg, methods=["POST"]),
        Route("/v1/policy/{policy_id:str}/slett", pa_slett_policy,
              methods=["POST"]),
        Route("/v1/policyutkast/{utkast_id:str}/forkast", pa_forkast_utkast,
              methods=["POST"]),
        Route("/v1/policyutkast/{utkast_id:str}/gjenapne", pa_gjenapne_utkast,
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
# 089 (M-35): `kontinuitet:read` er et lesescope i samme klasse — det
# åpner kun GET /v1/kontinuitet (tenantens egen beredskapstilstand).
LESESCOPES = frozenset({"decisions:read", "exceptions:read", "policy:read",
                        "security:read",
                        # M-6 PR-A: leseflaten for klassifiseringer,
                        # utkast og oppfølging (PR-D) — rent lesende.
                        "epost:read",
                        # 089 (M-35): kontinuitetsflaten leser tenantens
                        # egen beredskapstilstand — rent lesende.
                        "kontinuitet:read",
                        # 101 (M-13): avstemmingsflaten leser tenantens
                        # eget bank- og bilagsregister — rent lesende.
                        # Begrunnelsen for at scopet er nytt står i
                        # `autorisasjon.py`.
                        "okonomi:read",
                        # 102 (M-17): henvendelsens innhold — rent
                        # lesende, og skilt fra køens `decisions:read`
                        # fordi bare det ene er persondata.
                        "kundeservice:innhold"})

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
                                     "domains:adjudicate",
                                     # 038 §6: bestillingen skjer i flaten
                                     # (OIDC + CSRF); autoriteten ligger i
                                     # domenekontroll + beslutningsveien.
                                     "bestilling:opprett",
                                     # 044 §6: planen er stående INTENSJON
                                     # — hver kjøring policyvurderes som en
                                     # vanlig bestilling, så scopene gir
                                     # aldri stående utførelsesfullmakt.
                                     "plan:opprett", "plan:aktiver",
                                     "plan:gjenoppta",
                                     # M-6 PR-A: kildeforvaltning (koble
                                     # til/deaktiver postboks, PR-B) og
                                     # flatens dom over utkast (PR-D) —
                                     # menneskelige handlinger i flaten,
                                     # OIDC + CSRF som de andre. Carve-
                                     # outen slipper dem bare forbi den
                                     # generelle porten; endepunktene
                                     # finnes først i PR-B/D.
                                     "epost:kilde:administrer",
                                     "epost:utkast:behandle",
                                     # 089 (M-35): hendelseshåndteringen
                                     # er MENNESKELIG arbeid i flaten
                                     # (registrere, føre tidslinje,
                                     # lukke) — CSRF håndheves i
                                     # endepunktene (policyadmin-
                                     # formen); dørene bærer resten.
                                     "kontinuitet:write"})


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
                kapabilitet=auth.kapabilitet,
                # DETTE ER DET ENESTE ENDEPUNKTET SOM FØRER KLIENTENS NØKKEL
                # RETT INN I `idempotens` (Codex P1). Rommet er delt: en
                # annen vei — bestillingens gjenoppretting — LESER en rad
                # derfra som bevis på sin egen committede beslutning, og
                # nøkkelen den leser på er en deterministisk funksjon av det
                # klienten selv sendte. Uten flagget kunne en kaller med
                # `decision:write` hos samme tenant pre-skrive nøyaktig den
                # raden og få bestillingen til å arve en beslutning den
                # aldri tok. `kjerne.RESERVERTE_NOKKELROM` bærer regelen.
                klientvalgt_nokkel=True)
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
            if sakstype not in SAKSTYPER:
                return _feilsvar("request_feilformet", rid)
            if sakstype not in synlige_sakstyper(auth.scopes):
                # v3-delta pkt. 5: sikkerhets- og driftskøene er egne køer med
                # eget scope. `exceptions:read` alene ser dem aldri.
                tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                       scope="security:read")
                return _feilsvar("scope_mangler", rid)

            # `apen` er ikke en status i tabellen, men et SPØRSMÅL: «hva
            # venter fortsatt på noen?». Det må besvares her og ikke av
            # klienten, fordi filtrering skjer FØR `LIMIT`. Filtrerte
            # klienten selv, ville en side med åtte ferdigbehandlede saker
            # sett tom ut selv om det lå en uløst sak rett bak sidegrensen —
            # og `neste_cursor` ville aldri blitt fulgt.
            status = request.query_params.get("status")
            if status is not None and status not in ("ny", "under_behandling",
                                                     "løst", "avvist", "apen"):
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

            # `arsak` er MED (043, Codex P2): en sak født av
            # `sikre_sak_for_oppdrag` bærer hele sin grunn der — og fra 043
            # er tre av verdiene `kompensasjon_kreves`,
            # `irreversibel_utfort` og `reversibilitet_ukjent`, altså «et
            # menneske må rydde opp etter en handling som rakk å skje», «en
            # irreversibel handling ble rapportert utført» og «vi vet ikke
            # om virkningen kan reverseres». Uten kolonnen så listen
            # nøyaktig ut som en hvilken som helst arvet sak, og den
            # forskjellen er hele poenget med å føde saken.
            sql = ("SELECT id, ts, handling, kategori, prioritet, status,"
                   " sakstype, arsak FROM unntak"
                   " WHERE tenant=%s AND sakstype=%s")
            args: list = [auth.tenant, sakstype]
            if status == "apen":
                sql += " AND NOT (status = ANY(%s))"
                args.append(list(TERMINALE_UNNTAKSSTATUSER))
            elif status is not None:
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
                  "sakstype": r[6], "arsak": r[7]} for r in rader]
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
    # 063 (#165): fornyelsen autentiseres som claim/kvittering —
    # modultoken + claimets egen identitet i kroppen.
    ("POST", "/v1/oppdrag/forny"):           ORDRESCOPE + "<prefiks>",
    # #173: skriveveien inn i kandidatlagrene — samme autentisering som
    # forny/kvittering: modultoken + claimets identitet i kroppen.
    ("POST", "/v1/rekruttering/evaluering/{oppdrag_id:int}/slett"):
        "bestilling:opprett",
    ("POST", "/v1/rekruttering/evaluering/{oppdrag_id:int}/avbryt"):
        "bestilling:opprett",
    ("POST", "/v1/rekruttering/kandidatdokument"):
        ORDRESCOPE + "<prefiks>",
    ("POST", "/v1/rekruttering/kandidatartefakt"):
        ORDRESCOPE + "<prefiks>",
    ("POST", "/v1/artefakt"):                "artifacts:upload",
    ("GET",  "/v1/oversikt"):                "decisions:read",
    ("GET",  "/v1/nokkeltall"):              "decisions:read",
    ("GET",  "/v1/beslutninger"):            "decisions:read",
    ("GET",  "/v1/beslutninger/{id:int}"):   "decisions:read",
    # 038 §7: rapporten er evidensen bak tenantens egen beslutning.
    ("GET",  "/v1/rapport/{id:int}"):        "decisions:read",
    ("GET",  "/v1/unntak/{id:int}"):         "exceptions:read",
    ("GET",  "/v1/unntak/{id:int}/historikk"): "exceptions:read",
    ("POST", "/v1/unntak/{id:int}/handling"): "exceptions:approve",
    # PR-015 §3: cross-tenant domeneautoritet er sitt EGET scope.
    ("POST", "/v1/unntak/{id:int}/domeneattestasjon"): "domains:adjudicate",
    ("GET",  "/v1/policy/aktiv"):            "policy:read",
    # 047 (§6): historikken er NY rute bak EKSISTERENDE scope.
    ("GET",  "/v1/policy/{policy_id:str}/versjoner"): "policy:read",
    ("GET",  "/v1/policy/{policy_id:str}/diff"):      "policy:read",
    ("GET",  "/v1/policyadmin/editorgrunnlag"):       "policy:read",
    ("GET",  "/v1/policy/aktive"):           "policy:read",
    # M-57 utførelsesarmen: lesingen bak flatens svakeste ledd; signering
    # og blinding-avskruing bak mutasjonsscopet (056-kjeden + #159 gjør
    # resten av dømmingen inne i endepunktene).
    ("GET",  "/v1/rekruttering/prosesser"):  "decisions:read",
    # M-57s egen rapportflate ("ats"-diskriminatoren): evidens bak en
    # beslutning tenanten selv bestilte — samme scope som WCAG-rapporten.
    ("GET",  "/v1/rekruttering/rapport/{id:int}"): "decisions:read",
    ("GET",  "/v1/rekruttering/evalueringer"): "decisions:read",
    # Kandidatkortet (eiers bestilling 30/8): avmaskeringen leses av den
    # samme leseren som alt ser blindede funn — men hver lesing SPORES.
    ("GET",  "/v1/rekruttering/kandidatkort/{oppdrag_id:int}/{kandidat_id}"):
        "decisions:read",
    # Dokumentet bak kortet — alltid nedlasting, aldri rendring.
    ("GET",  "/v1/rekruttering/kandidatdokument/{oppdrag_id:int}/{dokument_id}"):
        "decisions:read",
    ("GET",  "/v1/rekruttering/stillingsprofiler"): "decisions:read",
    # Skriving av profilen er kundens/adminens bestillingsmyndighet —
    # samme scope som signeringen og inndata-reservasjonen.
    ("POST", "/v1/rekruttering/stillingsprofiler"): "bestilling:opprett",
    # 074: slett = enveis skjuling — samme myndighet som skrivingen.
    ("POST", "/v1/rekruttering/stillingsprofil/{profil_id}/slett"):
        "bestilling:opprett",
    # #160: kundeeid utsendingstekst — lesing bak flatens scope,
    # forfatting/sletting bak bestillingsmyndigheten.
    ("GET",  "/v1/rekruttering/utsendingstekster"): "decisions:read",
    ("POST", "/v1/rekruttering/utsendingstekster"): "bestilling:opprett",
    ("POST", "/v1/rekruttering/utsendingstekst/{tekst_id}/slett"):
        "bestilling:opprett",
    # Modulveien (060): retten er CLAIMET — ORDRESCOPE-klassen som
    # claim/kvittering; auth avgjøres i endepunktet (modultoken).
    ("POST", "/v1/inndata/hent-for-oppdrag/{oppdrag_id:int}"):
        ORDRESCOPE + "<prefiks>",
    ("POST", "/v1/inndata/reserver"):        "bestilling:opprett",
    ("PUT",  "/v1/inndata/opplast/{jti:str}"): "bestilling:opprett",
    ("POST", "/v1/rekruttering/prosesser/{prosess_id}/blinding"):
        "bestilling:opprett",
    ("POST", "/v1/rekruttering/lister"): "bestilling:opprett",
    ("POST", "/v1/rekruttering/lister/{liste_id:uuid}/signer"):
        "bestilling:opprett",
    # M-8 (082): kundens tidsvalg-administrasjon — lesing bak flatens
    # scope, mutasjon bak bestillingsmyndigheten (som resten av
    # rekrutteringsflaten).
    ("GET",  "/v1/rekruttering/tidsvalg"):   "decisions:read",
    ("POST", "/v1/rekruttering/tidsvalg/slots"): "bestilling:opprett",
    ("POST", "/v1/rekruttering/tidsvalg/slot/{slot_id}/deaktiver"):
        "bestilling:opprett",
    # M-8 (082): kandidatens offentlige dører — NY ruteklasse,
    # uautentisert utenom OIDC. Kapabiliteten i kroppen ER credentialet
    # (konstanttids MAC i den authenticator-eide defineren); ingen
    # cookie, ingen sesjon, ingen CSRF. Siden /tidsvalg er skallet.
    ("POST", "/v1/tidsvalg/oppslag"):        None,
    ("POST", "/v1/tidsvalg/velg"):           None,
    ("GET",  "/tidsvalg"):                   None,
    # Utrullingsplanen: kundens egen flate, derfor `decisions:read` (som ALLE
    # kunderollene har). Kontrollplanet på tvers krever i tillegg
    # `platform:admin`, og det avgjøres inne i endepunktet — det er en
    # utvidelse av svaret, ikke en annen inngang.
    ("GET",  "/v1/utrulling"):               "decisions:read",
    # M-31 (086): model card — plattformregisterets globale m31-rader bak
    # admin-lesescopet. Ingen tenantdata i svaret (registeret er
    # tenant-løst); mutasjonene finnes ikke som HTTP — de er
    # deploy-dører (registrer_golden_sett/sett_evalueringskrav/
    # registrer_evalueringskjoring, modules_admin).
    ("GET",  "/v1/modellstyring"):           "security:read",
    # 089 (M-35): kontinuitetsflaten. Lesing er tenantens egen
    # beredskapstilstand (`kontinuitet:read`, samme leseklasse som
    # beslutningene); de tre skriveveiene er MENNESKELIG kriseføring i
    # flaten og krever `kontinuitet:write` + CSRF + Idempotency-Key.
    # Lukkeruten er skilt fra postruten med vilje: å lukke en hendelse
    # er en annen handling enn å skrive i den, og en flate der de delte
    # endepunkt ville gjort lukkingen til en posttype blant andre.
    ("GET",  "/v1/kontinuitet"):             "kontinuitet:read",
    ("POST", "/v1/kontinuitet/hendelser"):   "kontinuitet:write",
    ("POST", "/v1/kontinuitet/hendelse/{hendelse_id:str}/post"):
        "kontinuitet:write",
    ("POST", "/v1/kontinuitet/hendelse/{hendelse_id:str}/lukk"):
        "kontinuitet:write",
    # 094 (M-5): malregisteret. INGEN nye scopes — M-5 generaliserer
    # 079_utsendingstekst, og 079s to ruter står alt her med
    # `decisions:read` for lesing og `bestilling:opprett` for forfatting
    # og skjuling. Et nytt scope er en registrering i autorisasjonslaget
    # med egen port; her finnes ingen myndighet 079 ikke har navngitt.
    #
    # UTFYLLINGEN BÆRER LESESCOPET, og det er ikke en forglemmelse: den
    # RETURNERER. `m5_fyll_mal` er STABLE (basen avviser enhver skriving
    # i kroppen), runtime har kun SELECT på de fire tabellene, og linjen
    # under er det tredje laget — ruten kan ikke vokse en skriveevne uten
    # at scopet endres i samme diff. POST og ikke GET fordi verdiene er
    # kundens data og ikke hører hjemme i en query-streng.
    ("GET",  "/v1/dokumentmal"):             "decisions:read",
    ("POST", "/v1/dokumentmal/familier"):    "bestilling:opprett",
    ("POST", "/v1/dokumentmal/versjoner"):   "bestilling:opprett",
    ("POST", "/v1/dokumentmal/versjon/{versjon_id:str}/publiser"):
        "bestilling:opprett",
    ("POST", "/v1/dokumentmal/versjon/{versjon_id:str}/trekk-tilbake"):
        "bestilling:opprett",
    ("POST", "/v1/dokumentmal/versjon/{versjon_id:str}/utfylling"):
        "decisions:read",
    # 096 (M-21): pliktregisteret. SCOPENE ER GJENBRUKT, IKKE NYE.
    # Registrering, kvittering og bortfall er BESTILLINGER i
    # plattformens forstand — de bærer `bestilling:opprett`, samme scope
    # som stillingsprofilen, utsendingsteksten og tidsvalg-slotene, og
    # det står alt i BROWSER_MUTASJONSSCOPES. Lesingen er kundens egen
    # tilstandsflate og bærer `decisions:read`, som ALLE kunderollene
    # har (utrullingsplanens begrunnelse, og det eneste `LESESCOPES`-
    # porten under godtar for en `/v1/`-GET).
    #
    # Sveipen står IKKE her, og det er en sikkerhetsdom og ikke en
    # manglende funksjon: `m21_koe_fristvarsler` er kryss-tenant og
    # kjøres av `disponit_varselsender` som et forpass — en fullmakt
    # web-API-rollen med vilje ikke har (038-reaperens snitt).
    ("GET",  "/v1/plikt"):                   "decisions:read",
    ("POST", "/v1/plikt"):                   "bestilling:opprett",
    ("POST", "/v1/plikt/{plikt_id:uuid}/lukk"): "bestilling:opprett",
    ("POST", "/v1/plikt/{plikt_id:uuid}/bortfall"): "bestilling:opprett",
    # 097 (M-12): tilgangsregisteret. SCOPENE ER GJENBRUKT, IKKE NYE —
    # men LESESCOPET ER ET ANNET ENN M-21s, og det er en dom:
    #
    # En pliktliste sier HVA som skal gjøres innen når; den er tenantens
    # driftstilstand, og enhver kunderolle skal se den (`decisions:read`).
    # Et tilgangsregister sier HVEM SOM HAR ADMIN PÅ HVA — et kart over
    # angrepsflaten, med kritikalitet per system og eier per nøkkel. Med
    # `decisions:read` ville hver `leser`, `godkjenner` og
    # `policyforvalter` fått det kartet. `security:read` er scopet
    # `admin` og `sikkerhet` har, det står i `LESESCOPES` (så en
    # browserøkt slipper gjennom `_autentiser`), og det er samme snitt
    # som driftstatus, datakvalitet og retensjonsregnskapet over.
    #
    # Registrering av objekt, tilgang og gjennomgang er BESTILLINGER i
    # plattformens forstand og bærer `bestilling:opprett` — samme scope
    # som stillingsprofilen og pliktregisterets skriveveier, og det står
    # alt i BROWSER_MUTASJONSSCOPES.
    #
    # SVEIPEN STÅR IKKE HER, og det er en sikkerhetsdom og ikke en
    # manglende funksjon: `m12_sveip_gjennomganger` er kryss-tenant og
    # kjøres av `disponit_tilgangssveip` fra sin egen timer — en fullmakt
    # web-API-rollen med vilje ikke har (038-reaperens snitt).
    ("GET",  "/v1/tilgang"):                 "security:read",
    ("POST", "/v1/tilgang"):                 "bestilling:opprett",
    ("POST", "/v1/tilgang/objekt"):          "bestilling:opprett",
    ("POST", "/v1/tilgang/{tilgang_id:uuid}/gjennomgang"):
        "bestilling:opprett",
    # 098 (M-22): lisensregisteret. SCOPENE ER M-21s, GJENBRUKT OG
    # VERIFISERT — ikke nye. Registrering, fornyelse og avslutning er
    # BESTILLINGER i plattformens forstand og bærer `bestilling:opprett`,
    # som `admin` alt har (autorisasjon.py) og som alt står i
    # BROWSER_MUTASJONSSCOPES. Lesingen er kundens egen tilstandsflate og
    # bærer `decisions:read` — scopet ALLE kunderollene har, og det
    # eneste `LESESCOPES`-porten under godtar for en `/v1/`-GET. Hvem som
    # eier hvilke lisenser og hva de koster er ikke administratorens
    # hemmelighet; å endre dem er hennes.
    #
    # Sveipen står IKKE her, og det er en sikkerhetsdom og ikke en
    # manglende funksjon: `m22_koe_utlopsvarsler` er kryss-tenant og
    # kjøres av `disponit_varselsender` som et forpass — en fullmakt
    # web-API-rollen med vilje ikke har (038-reaperens snitt).
    ("GET",  "/v1/lisens"):                  "decisions:read",
    ("POST", "/v1/lisens"):                  "bestilling:opprett",
    ("POST", "/v1/lisens/{lisens_id:uuid}/fornyelse"): "bestilling:opprett",
    ("POST", "/v1/lisens/{lisens_id:uuid}/avslutt"): "bestilling:opprett",
    # 100 (M-34): kontrollregisteret. SCOPENE ER GJENBRUKT, IKKE NYE.
    #
    # LESINGEN bærer `security:read` og ikke `decisions:read`, og det er
    # en dom: PR-008 §1 beskriver `security:read` som en valgfri
    # ops/compliance-scope på en TENANTBUNDET brukersesjon, og
    # `autorisasjon.py` beskriver rollen `sikkerhet` med nøyaktig de
    # ordene («Compliance/ops»). Kontrollregisteret er flaten det scopet
    # ble laget for. Kretsen er dessuten snevrere enn `decisions:read`
    # MED VILJE: avviksbeskrivelser og evidenshenvisninger er
    # revisjonsmateriale, ikke allmenn tilstandsinnsikt. Samme presedens
    # som `/v1/modellstyring`, `/v1/datakvalitet`, `/v1/retensjon` og
    # driftsrutene over.
    #
    # SKRIVINGEN bærer `bestilling:opprett` — samme scope som M-21s tre
    # skriveveier, alt i BROWSER_MUTASJONSSCOPES. Et nytt scope skal ikke
    # oppstå av vane.
    #
    # Sveipen står IKKE her, og det er en sikkerhetsdom og ikke en
    # manglende funksjon: `m34_sveip_etterprovinger` er kryss-tenant og
    # kjøres av `disponit_compliancesveip` fra sin egen timer — en
    # fullmakt web-API-rollen med vilje ikke har (038-reaperens snitt).
    ("GET",  "/v1/compliance"):              "security:read",
    ("POST", "/v1/compliance/kontroll"):     "bestilling:opprett",
    ("POST", "/v1/compliance/kontroll/{kontroll_id:uuid}/etterproving"):
        "bestilling:opprett",
    ("POST", "/v1/compliance/kontroll/{kontroll_id:uuid}/ikke-relevant"):
        "bestilling:opprett",
    # 101 (M-13): avstemmingsregisteret. LESINGEN bærer `okonomi:read`,
    # og det er det ene nye scopet i klynge 3 — begrunnelsen står i
    # `autorisasjon.py`: `decisions:read` er for vid (enhver `leser`
    # har den) og `security:read` er beskrevet som «Compliance/ops»,
    # altså noe annet enn økonomi. SKRIVINGEN bærer `bestilling:opprett`,
    # samme scope som M-21s og M-34s skriveveier, alt i
    # BROWSER_MUTASJONSSCOPES.
    #
    # Sveipen står IKKE her, og det er en sikkerhetsdom og ikke en
    # manglende funksjon: `m13_sveip_avstemming` er kryss-tenant og
    # kjøres av `disponit_avstemmingssveip` fra sin egen timer — en
    # fullmakt web-API-rollen med vilje ikke har (038-reaperens snitt).
    ("GET",  "/v1/avstemming"):              "okonomi:read",
    ("POST", "/v1/avstemming/konto"):        "bestilling:opprett",
    ("POST", "/v1/avstemming/bankpost"):     "bestilling:opprett",
    ("POST", "/v1/avstemming/bilag"):        "bestilling:opprett",
    ("POST", "/v1/avstemming/match"):        "bestilling:opprett",
    ("POST", "/v1/avstemming/match/{avstemming_id:uuid}/opphev"):
        "bestilling:opprett",
    # 102 (M-17): henvendelsesregisteret. KØEN bærer `decisions:read` —
    # tenantens alminnelige arbeidsflate, samme klasse som beslutningene.
    # INNHOLDET bærer `kundeservice:innhold`, og det skillet er
    # dommen: å se hvem som spurte og hvor gammelt det er, er noe annet
    # enn å lese hva de skrev. Skrivingen bærer `bestilling:opprett`.
    #
    # Sveipen står IKKE her: `m17_sveip_henvendelser` er kryss-tenant og
    # kjøres av `disponit_henvendelsessveip` fra sin egen timer.
    ("GET",  "/v1/kundeservice"):            "decisions:read",
    ("GET",  "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/innhold"):
        "kundeservice:innhold",
    ("GET",  "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/utkast"):
        "kundeservice:innhold",
    ("POST", "/v1/kundeservice/henvendelse"): "bestilling:opprett",
    ("POST", "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/klassifiser"):
        "bestilling:opprett",
    ("POST", "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/unntakskoe"):
        "bestilling:opprett",
    ("POST", "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/utkast/ny"):
        "bestilling:opprett",
    ("POST", "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/lukk"):
        "bestilling:opprett",
    ("POST", "/v1/kundeservice/utkast/{utkast_id:uuid}/dom"):
        "bestilling:opprett",
    # 103 (M-18): onboardingregisteret. LESINGEN bærer `decisions:read` —
    # hvem som gjør hva for en ny kunde er tenantens alminnelige
    # arbeidsflate, ikke administratorens hemmelighet, og det er ingen
    # persondata her utover et kundenavn og interne bruker-id-er.
    # SKRIVINGEN bærer `bestilling:opprett`.
    #
    # Sveipen står IKKE her: `m18_sveip_onboarding` er kryss-tenant og
    # kjøres av `disponit_onboardingsveip` fra sin egen timer.
    ("GET",  "/v1/onboarding"):              "decisions:read",
    ("GET",  "/v1/onboarding/lop/{lop_id:uuid}/steg"): "decisions:read",
    ("POST", "/v1/onboarding/mal"):          "bestilling:opprett",
    ("POST", "/v1/onboarding/mal/{mal_id:uuid}/steg"):
        "bestilling:opprett",
    ("POST", "/v1/onboarding/lop"):          "bestilling:opprett",
    ("POST", "/v1/onboarding/lop/{lop_id:uuid}/steg/{steg_nr:int}/eier"):
        "bestilling:opprett",
    ("POST", "/v1/onboarding/lop/{lop_id:uuid}/steg/{steg_nr:int}/fullfor"):
        "bestilling:opprett",
    ("POST", "/v1/onboarding/lop/{lop_id:uuid}/avslutt"):
        "bestilling:opprett",
    # 104 (M-23): fordringsregisteret. LESINGEN bærer `okonomi:read` —
    # scopet M-13 (101) innførte, og dette er nøyaktig kretsen det ble
    # laget for: hvem som skylder oss hva er virksomhetens pengestrøm,
    # ikke allmenn tilstandsinnsikt. GJENBRUKT, ikke nytt.
    # SKRIVINGEN bærer `bestilling:opprett`.
    #
    # Sveipen står IKKE her: `m23_sveip_fordringer` er kryss-tenant og
    # kjøres av `disponit_fordringssveip` fra sin egen timer. Sveipen
    # FLYTTER dessuten ingen trinn — den skriver funn.
    ("GET",  "/v1/fordring"):                "okonomi:read",
    ("GET",  "/v1/fordring/{fordring_id:uuid}/hendelser"): "okonomi:read",
    ("POST", "/v1/fordring"):                "bestilling:opprett",
    ("POST", "/v1/fordring/purreplan"):      "bestilling:opprett",
    ("POST", "/v1/fordring/{fordring_id:uuid}/betaling"):
        "bestilling:opprett",
    ("POST", "/v1/fordring/{fordring_id:uuid}/neste-trinn"):
        "bestilling:opprett",
    ("POST", "/v1/fordring/{fordring_id:uuid}/ettergi"):
        "bestilling:opprett",
    # 105 (M-24): leverandør- og SLA-registeret. LESINGEN bærer
    # `okonomi:read` — scopet M-13 (101) innførte og M-23 (104)
    # gjenbrukte. Hva vi har AVTALT å betale, og hva vi FAKTISK betaler,
    # er virksomhetens pengestrøm; ikke allmenn tilstandsinnsikt.
    # Gjenbrukt, ikke nytt: et eget `leverandor:read` ville delt den
    # samme kretsen i to uten at noen kunne si hvorfor.
    #
    # SKRIVINGEN bærer `bestilling:opprett`, samme presedens som
    # 096/100/101/102/103/104. Ingen av dem betaler noe — den handlingen
    # finnes ikke i v1.
    ("GET",  "/v1/leverandor"):                   "okonomi:read",
    ("GET",  "/v1/leverandor/{avtale_id:uuid}/leveranser"):
        "okonomi:read",
    ("POST", "/v1/leverandor/terskler"):          "bestilling:opprett",
    ("POST", "/v1/leverandor/part"):              "bestilling:opprett",
    ("POST", "/v1/leverandor/avtale"):            "bestilling:opprett",
    ("POST", "/v1/leverandor/{avtale_id:uuid}/leveranse"):
        "bestilling:opprett",
    ("POST", "/v1/leverandor/{avtale_id:uuid}/avslutt"):
        "bestilling:opprett",
    # 106 (M-14): fakturakontrollen. LESINGEN bærer `okonomi:read` —
    # scopet M-13 (101) innførte og M-23/M-24 gjenbrukte. Hva noen
    # krever av oss er virksomhetens pengestrøm.
    #
    # SKRIVINGEN bærer `bestilling:opprett`. Ingen av dem bokfører noe,
    # og ingen av dem attesterer: den handlingen finnes ikke i v1.
    ("GET",  "/v1/faktura"):                      "okonomi:read",
    ("GET",  "/v1/faktura/{faktura_id:uuid}/kontroller"): "okonomi:read",
    ("POST", "/v1/faktura"):                      "bestilling:opprett",
    ("POST", "/v1/faktura/terskler"):             "bestilling:opprett",
    ("POST", "/v1/faktura/mvasats"):              "bestilling:opprett",
    ("POST", "/v1/faktura/{faktura_id:uuid}/kontroll"):
        "bestilling:opprett",
    ("POST", "/v1/faktura/{faktura_id:uuid}/avgjor"):
        "bestilling:opprett",
    # 107 (M-25): prosjektregisteret. LESINGEN bærer `okonomi:read` —
    # hva et prosjekt koster og hva vi kan kreve for det er
    # virksomhetens pengestrøm. SKRIVINGEN bærer `bestilling:opprett`.
    # Ingen av dem fakturerer og ingen av dem attesterer.
    ("GET",  "/v1/prosjekt"):                     "okonomi:read",
    ("GET",  "/v1/prosjekt/{prosjekt_id:uuid}/milepaeler"):
        "okonomi:read",
    ("GET",  "/v1/prosjekt/{prosjekt_id:uuid}/arbeidsliste"):
        "okonomi:read",
    ("POST", "/v1/prosjekt"):                     "bestilling:opprett",
    ("POST", "/v1/prosjekt/terskler"):            "bestilling:opprett",
    ("POST", "/v1/prosjekt/{prosjekt_id:uuid}/betalingsplan"):
        "bestilling:opprett",
    ("POST", "/v1/prosjekt/{prosjekt_id:uuid}/milepael"):
        "bestilling:opprett",
    ("POST", "/v1/prosjekt/{prosjekt_id:uuid}/arbeid"):
        "bestilling:opprett",
    ("POST", "/v1/prosjekt/{prosjekt_id:uuid}/avslutt"):
        "bestilling:opprett",
    # 108 (M-26): prisboka. LESINGEN bærer `okonomi:read` — hva vi tar
    # betalt er virksomhetens pengestrøm. SKRIVINGEN bærer
    # `bestilling:opprett`. Ingen av dem genererer et tilbud.
    ("GET",  "/v1/prisbok"):                      "okonomi:read",
    ("GET",  "/v1/prisbok/{produkt_id:uuid}/historikk"): "okonomi:read",
    ("GET",  "/v1/prisbok/{produkt_id:uuid}/paa-dato"): "okonomi:read",
    ("POST", "/v1/prisbok/terskler"):             "bestilling:opprett",
    ("POST", "/v1/prisbok/produkt"):              "bestilling:opprett",
    ("POST", "/v1/prisbok/klausul"):              "bestilling:opprett",
    ("POST", "/v1/prisbok/{produkt_id:uuid}/pris"):
        "bestilling:opprett",
    ("POST", "/v1/prisbok/{produkt_id:uuid}/aktiv"):
        "bestilling:opprett",
    # 109 (M-27): lagerregisteret. LESINGEN bærer `okonomi:read` — en
    # beholdning er bundet kapital. SKRIVINGEN bærer
    # `bestilling:opprett`. Ingen av dem bestiller noe.
    ("GET",  "/v1/lager"):                        "okonomi:read",
    ("GET",  "/v1/lager/{vare_id:uuid}/bevegelser"): "okonomi:read",
    ("GET",  "/v1/lager/{vare_id:uuid}/paa-dato"): "okonomi:read",
    ("POST", "/v1/lager/terskler"):               "bestilling:opprett",
    ("POST", "/v1/lager/vare"):                   "bestilling:opprett",
    ("POST", "/v1/lager/{vare_id:uuid}/punkt"):   "bestilling:opprett",
    ("POST", "/v1/lager/{vare_id:uuid}/bevegelse"):
        "bestilling:opprett",
    ("POST", "/v1/lager/{vare_id:uuid}/telling"):
        "bestilling:opprett",
    ("POST", "/v1/lager/{vare_id:uuid}/aktiv"):   "bestilling:opprett",
    # 110 (M-42): kontoregisteret. LESINGEN bærer `okonomi:read` — den
    # som handler på «en leverandør har byttet konto» er den som
    # betaler. SKRIVINGEN bærer `bestilling:opprett`. Ingen av dem
    # stopper en betaling.
    ("GET",  "/v1/kontovakt"):                    "okonomi:read",
    ("GET",  "/v1/kontovakt/{mottaker_id:uuid}/historikk"):
        "okonomi:read",
    ("POST", "/v1/kontovakt/terskler"):           "bestilling:opprett",
    ("POST", "/v1/kontovakt/mottaker"):           "bestilling:opprett",
    ("POST", "/v1/kontovakt/oppgave/{oppgave_id:uuid}/verifikasjon"):
        "bestilling:opprett",
    ("POST", "/v1/kontovakt/{mottaker_id:uuid}/konto"):
        "bestilling:opprett",
    ("POST", "/v1/kontovakt/{mottaker_id:uuid}/aktiv"):
        "bestilling:opprett",
    # 111 (M-41): betalingsregisteret. LESINGEN bærer `okonomi:read`,
    # SKRIVINGEN `bestilling:opprett`. Ingen av dem refunderer.
    ("GET",  "/v1/betaling"):                     "okonomi:read",
    ("GET",  "/v1/betaling/{subjekt_id:uuid}/historikk"):
        "okonomi:read",
    ("POST", "/v1/betaling/terskler"):            "bestilling:opprett",
    ("POST", "/v1/betaling/subjekt"):             "bestilling:opprett",
    ("POST", "/v1/betaling/{subjekt_id:uuid}/status"):
        "bestilling:opprett",
    ("POST", "/v1/betaling/{subjekt_id:uuid}/abonnement"):
        "bestilling:opprett",
    ("POST", "/v1/betaling/{subjekt_id:uuid}/aktiv"):
        "bestilling:opprett",

    # 112 (M-19): adresseregisteret. LESINGEN bærer `okonomi:read`,
    # samme scope som 101 innførte og 111 gjenbrukte; SKRIVINGEN bærer
    # `bestilling:opprett`.
    # M-51 (119): LESINGEN bærer `okonomi:read`, SKRIVINGEN
    # `bestilling:opprett`. `/ferdigstill` er IKKE en innsendingsrute
    # — den setter en tilstand hos oss, og nekter uten minst én
    # forutsetning.
    ("GET",  "/v1/tilskudd"):                    "okonomi:read",
    ("GET",  "/v1/tilskudd/funn"):               "okonomi:read",
    ("GET",  "/v1/tilskudd/kildeposter"):        "okonomi:read",
    ("GET",  "/v1/tilskudd/estimat/{estimat_id:uuid}/poster"):
        "okonomi:read",
    ("GET",  "/v1/tilskudd/estimat/{estimat_id:uuid}/forutsetninger"):
        "okonomi:read",
    ("GET",  "/v1/tilskudd/{ordning_id:uuid}/estimater"):
        "okonomi:read",
    ("POST", "/v1/tilskudd/krav"):               "bestilling:opprett",
    ("POST", "/v1/tilskudd/ordning"):            "bestilling:opprett",
    ("POST", "/v1/tilskudd/kildepost"):          "bestilling:opprett",
    ("POST", "/v1/tilskudd/estimat/{estimat_id:uuid}/post"):
        "bestilling:opprett",
    ("POST", "/v1/tilskudd/estimat/{estimat_id:uuid}/forutsetning"):
        "bestilling:opprett",
    ("POST", "/v1/tilskudd/estimat/{estimat_id:uuid}/ferdigstill"):
        "bestilling:opprett",
    ("POST", "/v1/tilskudd/{ordning_id:uuid}/estimat"):
        "bestilling:opprett",
    ("POST", "/v1/tilskudd/{ordning_id:uuid}/aktiv"):
        "bestilling:opprett",
    ("POST", "/v1/tilskudd/{ordning_id:uuid}/funn/lukk"):
        "bestilling:opprett",
    # M-52 (122): LESINGEN bærer `okonomi:read`, SKRIVINGEN
    # `bestilling:opprett`. `/klart` er IKKE en deklarasjonsrute — den
    # setter en tilstand hos oss. `/forslag` NEKTER uten grunnlag, mot
    # en avviklet nomenklatur, og under tenantens terskel.
    # M-50 (124). LESING `okonomi:read`, SKRIVING `bestilling:opprett`.
    # INGEN RUTE HENTER — det finnes ingen `/hent`, ingen `/sok` og
    # ingen `/hoest`, og porten leser denne tabellen.
    ("GET",  "/v1/journal"):                     "okonomi:read",
    ("GET",  "/v1/journal/kilder"):              "okonomi:read",
    ("GET",  "/v1/journal/poster"):              "okonomi:read",
    ("GET",  "/v1/journal/funn"):                "okonomi:read",
    ("GET",  "/v1/journal/post/{post_id:uuid}/personer"):
        "okonomi:read",
    ("POST", "/v1/journal/krav"):                "bestilling:opprett",
    ("POST", "/v1/journal/kilde"):               "bestilling:opprett",
    ("POST", "/v1/journal/sak"):                 "bestilling:opprett",
    ("POST", "/v1/journal/post"):                "bestilling:opprett",
    ("POST", "/v1/journal/kilde/{kilde_id:uuid}/gyldig-til"):
        "bestilling:opprett",
    ("POST", "/v1/journal/person/{person_id:uuid}/anonymiser"):
        "bestilling:opprett",
    ("POST", "/v1/journal/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    # M-15 (128). LESING `okonomi:read`, SKRIVING `bestilling:opprett`.
    #
    # Lesescopet er `okonomi:read` og IKKE `security:read` som M-53s,
    # og skillet er datasettet: dette er bank, fordringer og
    # kontantbane — finansleserens eget bord. De eneste
    # personopplysningene er navnet på den som registrerte en
    # forpliktelse eller lukket et funn.
    #
    # INGEN RUTE SIER OPP NOE OG INGEN BETALER — det finnes ingen
    # `/iverksett`, og porten leser denne tabellen.
    ("GET",  "/v1/likviditet"):                  "okonomi:read",
    ("GET",  "/v1/likviditet/prognoser"):        "okonomi:read",
    ("GET",  "/v1/likviditet/poster"):           "okonomi:read",
    ("GET",  "/v1/likviditet/modeller"):         "okonomi:read",
    ("GET",  "/v1/likviditet/tiltak"):           "okonomi:read",
    ("GET",  "/v1/likviditet/funn"):             "okonomi:read",
    ("GET",  "/v1/likviditet/prognose/{prognose_id:uuid}/bane"):
        "okonomi:read",
    ("POST", "/v1/likviditet/krav"):             "bestilling:opprett",
    ("POST", "/v1/likviditet/modell"):           "bestilling:opprett",
    ("POST", "/v1/likviditet/post"):             "bestilling:opprett",
    ("POST", "/v1/likviditet/prognose"):         "bestilling:opprett",
    ("POST", "/v1/likviditet/tiltak"):           "bestilling:opprett",
    ("POST", "/v1/likviditet/prognose/{prognose_id:uuid}/maaling"):
        "bestilling:opprett",
    ("POST", "/v1/likviditet/tiltak/{tiltak_id:uuid}/vurder"):
        "bestilling:opprett",
    ("POST", "/v1/likviditet/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    # M-33 (130). INGEN RUTE TAR EN PERSONALAVGJØRELSE — det finnes
    # ingen `/ansett`, ingen `/siopp` og ingen `/iverksett`, og porten
    # leser denne tabellen.
    #
    # LESESCOPET ER `okonomi:read` OG IKKE noe strengere, og det er
    # ikke en forglemmelse: grunnlaget er `timeregistrering` (M-39),
    # som allerede leses med samme scope av lønnsmodulen. Et strengere
    # scope her ville skjult et AGGREGAT for noen som ser hver enkelt
    # rad — altså en grense som ser ut som vern og ikke er det.
    ("GET",  "/v1/prognose"):                   "okonomi:read",
    ("GET",  "/v1/prognose/prognoser"):         "okonomi:read",
    ("GET",  "/v1/prognose/modeller"):          "okonomi:read",
    ("GET",  "/v1/prognose/funn"):              "okonomi:read",
    ("GET",  "/v1/prognose/prognose/{prognose_id:uuid}/bane"):
        "okonomi:read",
    ("POST", "/v1/prognose/krav"):              "bestilling:opprett",
    ("POST", "/v1/prognose/modell"):            "bestilling:opprett",
    ("POST", "/v1/prognose/prognose"):          "bestilling:opprett",
    ("POST", "/v1/prognose/modell/{modell_id:uuid}/avvikle"):
        "bestilling:opprett",
    ("POST", "/v1/prognose/prognose/{prognose_id:uuid}/maaling"):
        "bestilling:opprett",
    ("POST", "/v1/prognose/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    # M-36 (132). INGEN RUTE IVERKSETTER ET TILTAK — det finnes ingen
    # `/iverksett`, og `tiltaksforslag.status` har ingen slik verdi.
    # INGEN RUTE ENDRER EN POLICY heller: fullmaktsutvidelse er
    # urepresenterbar, ikke frarådet, og porten leser denne tabellen.
    #
    # LESESCOPET ER `okonomi:read` fordi tiltakene anslås i ØRE og
    # rangeres på penger. Merk hva det IKKE gir: rangeringen bærer
    # `kilde_modul` og `kilde_funntype`, ikke funnenes innhold. En
    # finansleser ser AT det finnes tolv åpne HMS-funn, ikke hva de
    # gjelder.
    ("GET",  "/v1/optimalisator"):               "okonomi:read",
    ("GET",  "/v1/optimalisator/rangeringer"):   "okonomi:read",
    ("GET",  "/v1/optimalisator/tiltak"):        "okonomi:read",
    ("GET",  "/v1/optimalisator/modeller"):      "okonomi:read",
    ("GET",  "/v1/optimalisator/funn"):          "okonomi:read",
    ("GET",  "/v1/optimalisator/signaler"):      "okonomi:read",
    ("GET",  "/v1/optimalisator/rangering/{rangering_id:uuid}"):
        "okonomi:read",
    ("POST", "/v1/optimalisator/krav"):          "bestilling:opprett",
    ("POST", "/v1/optimalisator/modell"):        "bestilling:opprett",
    ("POST", "/v1/optimalisator/tiltak"):        "bestilling:opprett",
    ("POST", "/v1/optimalisator/stopp"):         "bestilling:opprett",
    ("POST", "/v1/optimalisator/rangering"):     "bestilling:opprett",
    ("POST", "/v1/optimalisator/modell/{modell_id:uuid}/avvikle"):
        "bestilling:opprett",
    ("POST", "/v1/optimalisator/tiltak/{tiltak_id:uuid}/vurder"):
        "bestilling:opprett",
    ("POST", "/v1/optimalisator/stopp/{stopp_id:uuid}/opphev"):
        "bestilling:opprett",
    ("POST", "/v1/optimalisator/rangering/{rangering_id:uuid}/effekt"):
        "bestilling:opprett",
    ("POST", "/v1/optimalisator/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    # M-7 (133). INGEN RUTE FATTER EN BESLUTNING — `/beslutning`
    # krever `besluttet_av`, og porten leser denne tabellen.
    #
    # LESESCOPET ER `security:read` OG IKKE `okonomi:read`: et referat
    # er hva navngitte mennesker sa i et møte, og et opptak er en
    # behandling av personopplysninger med hjemmel. Det er
    # compliance-leserens bord — samme vurdering som M-53 gjorde for
    # HMS-avvik.
    ("GET",  "/v1/mote"):                        "security:read",
    ("GET",  "/v1/mote/moter"):                  "security:read",
    ("GET",  "/v1/mote/hjemler"):                "security:read",
    ("GET",  "/v1/mote/aksjoner"):               "security:read",
    ("GET",  "/v1/mote/funn"):                   "security:read",
    ("GET",  "/v1/mote/{mote_id:uuid}/referat"): "security:read",
    ("POST", "/v1/mote/krav"):                   "bestilling:opprett",
    ("POST", "/v1/mote/hjemmel"):                "bestilling:opprett",
    ("POST", "/v1/mote/mote"):                   "bestilling:opprett",
    ("POST", "/v1/mote/hjemmel/{hjemmel_id:uuid}/avslutt"):
        "bestilling:opprett",
    ("POST", "/v1/mote/aksjon/{aksjon_id:uuid}/lukk"):
        "bestilling:opprett",
    ("POST", "/v1/mote/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    ("POST", "/v1/mote/{mote_id:uuid}/opptak"):
        "bestilling:opprett",
    ("POST", "/v1/mote/{mote_id:uuid}/referatpunkt"):
        "bestilling:opprett",
    ("POST", "/v1/mote/{mote_id:uuid}/beslutning"):
        "bestilling:opprett",
    ("POST", "/v1/mote/{mote_id:uuid}/aksjon"):
        "bestilling:opprett",
    ("GET",  "/v1/innhold"):                     "security:read",
    ("GET",  "/v1/innhold/sider"):               "security:read",
    ("GET",  "/v1/innhold/kilder"):              "security:read",
    ("GET",  "/v1/innhold/funn"):                "security:read",
    ("GET",  "/v1/innhold/utkast/{utkast_id:uuid}"): "security:read",
    ("POST", "/v1/innhold/krav"):                "bestilling:opprett",
    ("POST", "/v1/innhold/kilde"):               "bestilling:opprett",
    ("POST", "/v1/innhold/utkast"):              "bestilling:opprett",
    ("POST", "/v1/innhold/utkast/{utkast_id:uuid}/paastand"):
        "bestilling:opprett",
    ("POST", "/v1/innhold/utkast/{utkast_id:uuid}/visning"):
        "bestilling:opprett",
    ("POST", "/v1/innhold/utkast/{utkast_id:uuid}/klar"):
        "bestilling:opprett",
    ("POST", "/v1/innhold/utkast/{utkast_id:uuid}/publiser"):
        "bestilling:opprett",
    ("POST", "/v1/innhold/publisering/{publisering_id:uuid}/tilbake"):
        "bestilling:opprett",
    ("POST", "/v1/innhold/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    ("GET",  "/v1/telefoni"):                    "security:read",
    ("GET",  "/v1/telefoni/samtaler"):           "security:read",
    ("GET",  "/v1/telefoni/hjemler"):            "security:read",
    ("GET",  "/v1/telefoni/regler"):             "security:read",
    ("GET",  "/v1/telefoni/funn"):               "security:read",
    ("GET",  "/v1/telefoni/samtale/{samtale_id:uuid}/transkripsjon"):
        "security:read",
    ("POST", "/v1/telefoni/krav"):               "bestilling:opprett",
    ("POST", "/v1/telefoni/hjemmel"):            "bestilling:opprett",
    ("POST", "/v1/telefoni/regel"):              "bestilling:opprett",
    ("POST", "/v1/telefoni/samtale"):            "bestilling:opprett",
    ("POST", "/v1/telefoni/regel/{regel_id:uuid}/avvikle"):
        "bestilling:opprett",
    ("POST", "/v1/telefoni/samtale/{samtale_id:uuid}/avslutt"):
        "bestilling:opprett",
    ("POST", "/v1/telefoni/samtale/{samtale_id:uuid}/opptak"):
        "bestilling:opprett",
    ("POST", "/v1/telefoni/samtale/{samtale_id:uuid}/linje"):
        "bestilling:opprett",
    ("POST", "/v1/telefoni/samtale/{samtale_id:uuid}/eskaler"):
        "bestilling:opprett",
    ("POST", "/v1/telefoni/eskalering/{eskalering_id:uuid}/lukk"):
        "bestilling:opprett",
    ("POST", "/v1/telefoni/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    ("GET",  "/v1/esg"):                         "security:read",
    ("GET",  "/v1/esg/perioder"):                "security:read",
    ("GET",  "/v1/esg/faktorer"):                "security:read",
    ("GET",  "/v1/esg/funn"):                    "security:read",
    ("GET",  "/v1/esg/periode/{periode_id:uuid}/maalinger"):
        "security:read",
    ("GET",  "/v1/esg/periode/{periode_id:uuid}/paastander"):
        "security:read",
    ("POST", "/v1/esg/krav"):                    "bestilling:opprett",
    ("POST", "/v1/esg/kilde"):                   "bestilling:opprett",
    ("POST", "/v1/esg/periode"):                 "bestilling:opprett",
    ("POST", "/v1/esg/faktor"):                  "bestilling:opprett",
    ("POST", "/v1/esg/faktor/{faktor_id:uuid}/avvikle"):
        "bestilling:opprett",
    ("POST", "/v1/esg/periode/{periode_id:uuid}/lukk"):
        "bestilling:opprett",
    ("POST", "/v1/esg/periode/{periode_id:uuid}/maaling"):
        "bestilling:opprett",
    ("POST", "/v1/esg/periode/{periode_id:uuid}/paastand"):
        "bestilling:opprett",
    ("POST", "/v1/esg/periode/{periode_id:uuid}/sammenstill"):
        "bestilling:opprett",
    ("POST", "/v1/esg/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    # M-53 (127). INGEN RUTE VARSLER EN MYNDIGHET — det finnes ingen
    # `/send`, ingen `/innsending` og ingen `/varsle`, og porten leser
    # denne tabellen.
    #
    # LESESCOPET ER `security:read` OG IKKE `okonomi:read`, og det er
    # en RETTELSE av min egen første utgave (CodeRabbit). Jeg skrev av
    # M-50-raden over uten å se at datasettet er et helt annet:
    #
    #   `GET /v1/hms/avvik` returnerer `beskrivelse`,
    #   `helseopplysninger` og `melder_navn`. 127s eget filhode sier at
    #   en skademelding inneholder særlige kategorier etter GDPR art. 9
    #   «ikke som en mulighet — som normaltilfellet», og at et avvik
    #   kan være et varsel etter arbeidsmiljøloven kap. 2 A.
    #
    #   `okonomi:read` er FINANSLESERENS scope (M-13, 101: bank- og
    #   bilagsregistre). Å legge helseopplysninger om navngitte
    #   ansatte der ville vært samme feil som M-30-raden under
    #   beskriver: lesetilgang for alle som skal se noe helt annet.
    #
    # `security:read` er compliance/ops-klassen — samme sted M-12
    # (tilgangskartet), M-34 (avviksbeskrivelser) og M-30
    # (personvernforespørslene) ligger, og et HMS-ansvarlig
    # personvernombuds arbeidsflate hører hjemme nettopp der.
    #
    # SKRIVEVEIENE BEHOLDER `bestilling:opprett`, og det er MED VILJE:
    # den som skal MELDE et avvik er en hvilken som helst ansatt, og
    # skulle meldingen krevd `security:read` ville en anonym melding
    # måttet gå gjennom den HMS-ansvarlige — altså ikke vært anonym.
    #
    # PRISEN STÅR SKREVET, for den er reell: flaten viser REGISTERET og
    # skjemaet på samme side, og siden må gjerdes av det STRENGESTE
    # den viser. En egen, lavt gjerdet meldeflate for alle ansatte er
    # en v2-jobb — ikke noe jeg finner på klokka seks om morgenen for
    # å slippe å skrive ned at den mangler.
    ("GET",  "/v1/hms"):                         "security:read",
    ("GET",  "/v1/hms/avvik"):                   "security:read",
    ("GET",  "/v1/hms/regelverk"):               "security:read",
    ("GET",  "/v1/hms/funn"):                    "security:read",
    ("GET",  "/v1/hms/avvik/{avvik_id:uuid}/tiltak"):
        "security:read",
    ("GET",  "/v1/hms/avvik/{avvik_id:uuid}/oppbevaringsgrunnlag"):
        "security:read",
    ("POST", "/v1/hms/krav"):                    "bestilling:opprett",
    ("POST", "/v1/hms/regelverk"):               "bestilling:opprett",
    ("POST", "/v1/hms/avvik"):                   "bestilling:opprett",
    ("POST", "/v1/hms/avvik/{avvik_id:uuid}/tiltak"):
        "bestilling:opprett",
    ("POST", "/v1/hms/avvik/{avvik_id:uuid}/anonymiser"):
        "bestilling:opprett",
    ("POST", "/v1/hms/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    # M-47 (123). LESING `okonomi:read`, SKRIVING `bestilling:opprett`.
    # INGEN RUTE SENDER INN — det finnes ingen `/send`, ingen
    # `/innsending` og ingen `/signer`, og porten leser denne tabellen.
    ("GET",  "/v1/myndighet"):                   "okonomi:read",
    ("GET",  "/v1/myndighet/regelverk"):         "okonomi:read",
    ("GET",  "/v1/myndighet/plikter"):           "okonomi:read",
    ("GET",  "/v1/myndighet/funn"):              "okonomi:read",
    ("POST", "/v1/myndighet/krav"):              "bestilling:opprett",
    ("POST", "/v1/myndighet/regelverk"):         "bestilling:opprett",
    ("POST", "/v1/myndighet/regelverk/{regelverk_id:uuid}/gyldig-til"):
        "bestilling:opprett",
    ("POST", "/v1/myndighet/plikttype"):         "bestilling:opprett",
    ("POST", "/v1/myndighet/plikt"):             "bestilling:opprett",
    ("POST", "/v1/myndighet/plikt/{plikt_id:uuid}/bevis"):
        "bestilling:opprett",
    ("POST", "/v1/myndighet/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    ("GET",  "/v1/toll"):                        "okonomi:read",
    ("GET",  "/v1/toll/funn"):                   "okonomi:read",
    ("GET",  "/v1/toll/nomenklatur/{nomenklatur_id:uuid}/varenummer"):
        "okonomi:read",
    ("GET",  "/v1/toll/forslag/{forslag_id:uuid}/grunner"):
        "okonomi:read",
    ("GET",  "/v1/toll/vare/{vare_id:uuid}/forslag"):
        "okonomi:read",
    ("POST", "/v1/toll/krav"):                   "bestilling:opprett",
    ("POST", "/v1/toll/nomenklatur"):            "bestilling:opprett",
    ("POST", "/v1/toll/varenummer"):             "bestilling:opprett",
    ("POST", "/v1/toll/vare"):                   "bestilling:opprett",
    ("POST", "/v1/toll/nomenklatur/{nomenklatur_id:uuid}/gyldig-til"):
        "bestilling:opprett",
    ("POST", "/v1/toll/vare/{vare_id:uuid}/forslag"):
        "bestilling:opprett",
    ("POST", "/v1/toll/forslag/{forslag_id:uuid}/klart"):
        "bestilling:opprett",
    ("POST", "/v1/toll/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    # M-54 (121): LESINGEN bærer `okonomi:read`, SKRIVINGEN
    # `bestilling:opprett`. `/klar` er IKKE en utsendingsrute — den
    # setter en tilstand hos oss, og signaturen finnes ikke i v1.
    # `/valider` NEKTER mot et utløpt regelsett.
    ("GET",  "/v1/ehf"):                         "okonomi:read",
    ("GET",  "/v1/ehf/funn"):                    "okonomi:read",
    ("GET",  "/v1/ehf/regelsett/{regelsett_id:uuid}/regler"):
        "okonomi:read",
    ("GET",  "/v1/ehf/validering/{validering_id:uuid}/avvik"):
        "okonomi:read",
    ("GET",  "/v1/ehf/dokument/{dokument_id:uuid}/valideringer"):
        "okonomi:read",
    ("POST", "/v1/ehf/krav"):                    "bestilling:opprett",
    ("POST", "/v1/ehf/regelsett"):               "bestilling:opprett",
    ("POST", "/v1/ehf/regel"):                   "bestilling:opprett",
    ("POST", "/v1/ehf/dokument"):                "bestilling:opprett",
    ("POST", "/v1/ehf/regelsett/{regelsett_id:uuid}/gyldig-til"):
        "bestilling:opprett",
    ("POST", "/v1/ehf/dokument/{dokument_id:uuid}/felter"):
        "bestilling:opprett",
    ("POST", "/v1/ehf/dokument/{dokument_id:uuid}/valider"):
        "bestilling:opprett",
    ("POST", "/v1/ehf/avvik/{avvik_id:uuid}/retting"):
        "bestilling:opprett",
    ("POST", "/v1/ehf/retting/{retting_id:uuid}/klar"):
        "bestilling:opprett",
    ("POST", "/v1/ehf/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    # M-55 (120): LESINGEN bærer `okonomi:read`, SKRIVINGEN
    # `bestilling:opprett`. `/henvis` er IKKE en utsendingsrute — den
    # fester en peker til M-37s unntakskø, og der beslutter et
    # menneske. Det finnes ingen rute som sender et krav.
    ("GET",  "/v1/merkevare"):                   "okonomi:read",
    ("GET",  "/v1/merkevare/funn"):              "okonomi:read",
    ("GET",  "/v1/merkevare/bevaringskopier"):   "okonomi:read",
    ("GET",  "/v1/merkevare/varsler"):           "okonomi:read",
    ("GET",  "/v1/merkevare/funn/{funn_id:uuid}/vurderinger"):
        "okonomi:read",
    ("GET",  "/v1/merkevare/{merkevare_id:uuid}/funn"):
        "okonomi:read",
    ("POST", "/v1/merkevare/krav"):              "bestilling:opprett",
    ("POST", "/v1/merkevare/merke"):             "bestilling:opprett",
    ("POST", "/v1/merkevare/bevaringskopi"):     "bestilling:opprett",
    ("POST", "/v1/merkevare/funn"):              "bestilling:opprett",
    ("POST", "/v1/merkevare/funn/{funn_id:uuid}/vurder"):
        "bestilling:opprett",
    ("POST", "/v1/merkevare/funn/{funn_id:uuid}/henvis"):
        "bestilling:opprett",
    ("POST", "/v1/merkevare/funn/{funn_id:uuid}/lukk"):
        "bestilling:opprett",
    ("POST", "/v1/merkevare/varsel/{varsel_id:uuid}/lukk"):
        "bestilling:opprett",
    ("POST", "/v1/merkevare/{merkevare_id:uuid}/aktiv"):
        "bestilling:opprett",
    # M-46 (118): LESINGEN bærer `okonomi:read`, SKRIVINGEN
    # `bestilling:opprett`. `/klart` er IKKE en innsendingsrute — den
    # setter en tilstand hos oss, og nekter så lenge et absolutt krav
    # står udekket.
    ("GET",  "/v1/anbud"):                       "okonomi:read",
    ("GET",  "/v1/anbud/funn"):                  "okonomi:read",
    ("GET",  "/v1/anbud/kilder"):                "okonomi:read",
    ("GET",  "/v1/anbud/{anbud_id:uuid}/krav"):  "okonomi:read",
    ("GET",  "/v1/anbud/{anbud_id:uuid}/utkast"): "okonomi:read",
    ("POST", "/v1/anbud/profil"):                "bestilling:opprett",
    ("POST", "/v1/anbud/registrer"):             "bestilling:opprett",
    ("POST", "/v1/anbud/kilde"):                 "bestilling:opprett",
    ("POST", "/v1/anbud/utkast/{utkast_id:uuid}/punkt"):
        "bestilling:opprett",
    ("POST", "/v1/anbud/utkast/{utkast_id:uuid}/klart"):
        "bestilling:opprett",
    ("POST", "/v1/anbud/{anbud_id:uuid}/krav/ny"):
        "bestilling:opprett",
    ("POST", "/v1/anbud/{anbud_id:uuid}/utkast/ny"):
        "bestilling:opprett",
    ("POST", "/v1/anbud/{anbud_id:uuid}/aktiv"):
        "bestilling:opprett",
    ("POST", "/v1/anbud/{anbud_id:uuid}/funn/lukk"):
        "bestilling:opprett",
    # M-49 (117): LESINGEN bærer `okonomi:read`, SKRIVINGEN
    # `bestilling:opprett`. `lister` og `treff` er bevisst leseveier
    # for tenanten selv: «sto de på lista DEN DAGEN» er spørsmålet et
    # tilsyn stiller, og den som eier dataene skal kunne svare uten å
    # spørre oss.
    ("GET",  "/v1/sanksjon"):                    "okonomi:read",
    ("GET",  "/v1/sanksjon/funn"):               "okonomi:read",
    ("GET",  "/v1/sanksjon/lister"):             "okonomi:read",
    ("GET",  "/v1/sanksjon/{subjekt_id:uuid}/kontroller"):
        "okonomi:read",
    ("GET",  "/v1/sanksjon/{subjekt_id:uuid}/treff"): "okonomi:read",
    ("POST", "/v1/sanksjon/krav"):               "bestilling:opprett",
    ("POST", "/v1/sanksjon/liste"):              "bestilling:opprett",
    ("POST", "/v1/sanksjon/subjekt"):            "bestilling:opprett",
    ("POST", "/v1/sanksjon/treff/{treff_id:uuid}/avklaring"):
        "bestilling:opprett",
    ("POST", "/v1/sanksjon/{subjekt_id:uuid}/kontroll"):
        "bestilling:opprett",
    ("POST", "/v1/sanksjon/{subjekt_id:uuid}/aktiv"):
        "bestilling:opprett",
    ("POST", "/v1/sanksjon/{subjekt_id:uuid}/funn/lukk"):
        "bestilling:opprett",
    # M-48 (116): LESINGEN bærer `okonomi:read`, SKRIVINGEN
    # `bestilling:opprett` — samme presedens som 101/111/112.
    # `oppslagslogg` er bevisst en LESEVEI for tenanten selv: et
    # unntak ingen kan etterprøve er ikke et unntak.
    ("GET",  "/v1/motpart"):                     "okonomi:read",
    ("GET",  "/v1/motpart/funn"):                "okonomi:read",
    ("GET",  "/v1/motpart/{motpart_id:uuid}/historikk"):
        "okonomi:read",
    ("GET",  "/v1/motpart/{motpart_id:uuid}/oppslagslogg"):
        "okonomi:read",
    ("POST", "/v1/motpart/krav"):                "bestilling:opprett",
    ("POST", "/v1/motpart/registrer"):           "bestilling:opprett",
    ("POST", "/v1/motpart/versjon/{versjon_id:uuid}/vurdering"):
        "bestilling:opprett",
    ("POST", "/v1/motpart/{motpart_id:uuid}/oppslag"):
        "bestilling:opprett",
    ("POST", "/v1/motpart/{motpart_id:uuid}/deaktiver"):
        "bestilling:opprett",
    ("POST", "/v1/motpart/{motpart_id:uuid}/funn/lukk"):
        "bestilling:opprett",
    ("GET",  "/v1/adresse"):                     "okonomi:read",
    ("GET",  "/v1/adresse/{subjekt_id:uuid}/historikk"):
        "okonomi:read",
    ("GET",  "/v1/adresse/versjon/{versjon_id:uuid}/kontroller"):
        "okonomi:read",
    ("POST", "/v1/adresse/krav"):                "bestilling:opprett",
    ("POST", "/v1/adresse/subjekt"):             "bestilling:opprett",
    ("POST", "/v1/adresse/{subjekt_id:uuid}/versjon"):
        "bestilling:opprett",
    ("POST", "/v1/adresse/{subjekt_id:uuid}/aktiv"):
        "bestilling:opprett",
    ("POST", "/v1/adresse/versjon/{versjon_id:uuid}/kontroll"):
        "bestilling:opprett",

    # 113 (M-39): lønnsgrunnlaget. LESINGEN bærer `okonomi:read`, samme
    # scope som 101 innførte og 111/112 gjenbrukte; SKRIVINGEN bærer
    # `bestilling:opprett`.
    ("GET",  "/v1/lonn"):                        "okonomi:read",
    ("GET",  "/v1/lonn/{taker_id:uuid}/dager"):  "okonomi:read",
    ("GET",  "/v1/lonn/{taker_id:uuid}/historikk"):
        "okonomi:read",
    ("GET",  "/v1/lonn/{taker_id:uuid}/planer"): "okonomi:read",
    ("POST", "/v1/lonn/terskler"):               "bestilling:opprett",
    ("POST", "/v1/lonn/taker"):                  "bestilling:opprett",
    ("POST", "/v1/lonn/{taker_id:uuid}/plan"):   "bestilling:opprett",
    ("POST", "/v1/lonn/{taker_id:uuid}/timer"):  "bestilling:opprett",
    ("POST", "/v1/lonn/{taker_id:uuid}/aktiv"):  "bestilling:opprett",

    # 114 (M-44): kampanjeregisteret. LESINGEN bærer `okonomi:read`,
    # samme scope som 101 innførte og 111–113 gjenbrukte; SKRIVINGEN
    # bærer `bestilling:opprett`.
    ("GET",  "/v1/kampanje"):                    "okonomi:read",
    ("GET",  "/v1/kampanje/mottaker/{mottaker_id:uuid}/samtykke"):
        "okonomi:read",
    ("GET",  "/v1/kampanje/mottaker/{mottaker_id:uuid}/samtykke/"
             "{dag:str}"): "okonomi:read",
    ("POST", "/v1/kampanje/grense"):             "bestilling:opprett",
    ("POST", "/v1/kampanje/mottaker"):           "bestilling:opprett",
    ("POST", "/v1/kampanje/kampanje"):           "bestilling:opprett",
    ("POST", "/v1/kampanje/mottaker/{mottaker_id:uuid}/samtykke"):
        "bestilling:opprett",
    ("POST", "/v1/kampanje/mottaker/{mottaker_id:uuid}/aktiv"):
        "bestilling:opprett",
    ("POST", "/v1/kampanje/kampanje/{kampanje_id:uuid}/avlys"):
        "bestilling:opprett",
    ("POST", "/v1/kampanje/kampanje/{kampanje_id:uuid}/plan"):
        "bestilling:opprett",
    # M-10 (090) / M-11 (091): plattformdriftens eget innsyn — backupens
    # verifiseringshistorikk og selvtestens runder, bak SAMME
    # admin-lesescope som model card over. Ingen tenantdata i noen av
    # svarene (begge tabellene er plattformskop, uten tenant-kolonne);
    # tenantkonteksten kreves likevel av dørene, fordi RETTEN til å
    # spørre er øktens selv når dataene ikke er det.
    #
    # Mutasjonene finnes ikke som HTTP for noen av dem: verifiseringer
    # skrives av `disponit-backupstatus.service` (rollen
    # `disponit_driftstatus`) og runder av `disponit-selvtest.service`
    # (rollen `disponit_selvtest`) — to fullmakter web-API-et ikke har.
    ("GET",  "/v1/drift/backup"):            "security:read",
    ("GET",  "/v1/drift/selvtest"):          "security:read",
    # M-3 (092): datakvalitetsprofilen. Lesing er tenantens egen
    # tilstand + det globale kvalitetsregisteret, bak SAMME admin-
    # lesescope som model card og driftstatus over. `platform:admin`
    # UTVIDER svaret med funnlisten på tvers, og den avgjørelsen tas
    # inne i endepunktet (/v1/utrulling-presedensen) — scopet her er
    # flatens svakeste ledd, som resten av tabellen.
    #
    # Mutasjonen finnes ikke som HTTP: profileringen kjøres av
    # `disponit-kvalitetsprofil.service` (rollen
    # `disponit_kvalitetsmaaler`, EXECUTE på nøyaktig én funksjon), og
    # kvalitetsregisteret endres kun i migrasjon. v1 RETTER INGENTING og
    # blokkerer ingen bestilling — en skriverute her ville vært det
    # første steget bort fra den dommen.
    ("GET",  "/v1/datakvalitet"):            "security:read",
    # M-4 (093): retensjonsregnskapet — samme klasse som driftstatus over.
    # INGEN nytt scope, og BEVISST ikke `platform:admin`: det scopet står
    # ikke i `LESESCOPES`, og en browserøkt mot et scope utenfor det
    # settet avvises i `_autentiser` — en rute deklarert `platform:admin`
    # ville gitt 403 for hver eneste innlogging. Kontrollplanet
    # (katalogtall, alle tenanters beholdning, hele funnlisten) er derfor
    # en UTVIDELSE av svaret, avgjort i `retensjon.svar_for`, akkurat som
    # for `/v1/utrulling`.
    #
    # Mutasjonen finnes ikke som HTTP: målingen skrives av
    # `disponit-lagermaaling.service` (rollen `disponit_lagermaaler`), og
    # registerets dommer felles i MIGRASJON — to fullmakter web-API-et
    # ikke har.
    ("GET",  "/v1/retensjon"):               "security:read",
    # M-9 (095): begrepsregisteret — bedriftens egen ordliste med eier,
    # kilde og gyldighetsdato, og et fritekstsøk over den. Scopet er
    # `decisions:read`, samme klasse som `/v1/utrulling`: dette er
    # kundens egen referansetekst, og ALLE kunderollene skal kunne slå
    # opp et begrep. Begrunnelsen for å IKKE registrere et eget
    # `kunnskap:read` står i `lesing.kunnskap`. Ingen skriverute finnes:
    # dørene i 095 er REVOKEt fra runtime-rollen i `migrer.py`.
    ("GET",  "/v1/kunnskap"):                "decisions:read",
    # M-30 (099): forespørselsregisteret — hvem som har krevd innsyn i,
    # retting av eller sletting av sine egne personopplysninger, med
    # frist, eier og skrevet svar.
    #
    # LESESCOPET ER `security:read`, IKKE `decisions:read`, og det er en
    # dom og ikke en avskrift av M-21-raden over. `decisions:read` har
    # ALLE kunderollene i `autorisasjon.py` — også `leser`,
    # `godkjenner` og `policyforvalter` — og det scopet er «kundens egen
    # tilstandsflate»: beslutninger, rapporter, utrullingsplanen,
    # ordlisten, pliktregisteret. Å legge DETTE registeret der ville
    # gitt hver eneste innlogget bruker lesetilgang til hvem i
    # virksomheten som har bedt om sletting av sine personopplysninger.
    # `security:read` har `sikkerhet` og `admin` — compliance/ops-
    # klassen, den samme `/v1/drift/*`, `/v1/datakvalitet` og
    # `/v1/retensjon` alt ligger i, og et personvernombuds arbeidsflate
    # hører hjemme nettopp der. Scopet står dessuten i `LESESCOPES`, som
    # er det `_autentiser` krever av en browserøkt; `platform:admin`
    # gjør ikke det og ville gitt 403 for hver eneste innlogging
    # (093-raden over sier det samme).
    #
    # SKRIVEVEIENE GJENBRUKER `bestilling:opprett` — samme scope som
    # pliktregisterets tre, og det står alt i BROWSER_MUTASJONSSCOPES.
    # Konsekvensen er tilsiktet: `sikkerhet` kan SE registeret og kan
    # IKKE endre det. Å lese hvilke frister som løper er tilsyn; å svare
    # på vegne av virksomheten er myndighet.
    #
    # Sveipen står IKKE her, og det er en sikkerhetsdom og ikke en
    # manglende funksjon: `m30_sveip_frister` er kryss-tenant og kjøres
    # av `disponit_personvernsveip` fra sin egen timer — en fullmakt
    # web-API-rollen med vilje ikke har (038-reaperens snitt).
    ("GET",  "/v1/personvern"):              "security:read",
    ("POST", "/v1/personvern"):              "bestilling:opprett",
    ("POST", "/v1/personvern/{sak_id:uuid}/svar"): "bestilling:opprett",
    ("POST", "/v1/personvern/{sak_id:uuid}/avvis"): "bestilling:opprett",
    ("POST", "/v1/personvern/{sak_id:uuid}/forleng"): "bestilling:opprett",
    # PR-013: policyadministrasjon. write/activate er ADSKILTE (V6); lesing er
    # policy:read. Verifiseres per-endepunkt av _autentiser + CSRF.
    ("GET",  "/v1/policymaler"):             "policy:read",
    # 035: modul-onboarding. Maskin-/ops-ruter; scope-porten håndheves
    # inne i endepunktene (Bearer/modultoken, aldri browserøkt) — samme
    # deklarasjonsform som /v1/oppdrag/*. Innløsningen autentiseres av
    # selve engangshemmeligheten, rotasjonen av modultokenet.
    ("POST", "/v1/bestilling"):              "bestilling:opprett",
    # 039: selvbetjent domeneverifisering — samme autoritet som bestilling
    # (domeneregisteret ER porten bestillingsveien håndhever).
    # GET er en LESERUTE og følger leseinvariantens scopes (pr008-porten):
    # å SE domenelisten er lesing av egen tilstand; å ENDRE den krever
    # bestilling:opprett. Flaten selv ligger uansett bak admin-ruten.
    ("GET",  "/v1/domener"):                 "decisions:read",
    ("GET",  "/v1/domeneovertakelse/saker"): "domains:adjudicate",
    ("GET",  "/v1/plan"):                    "decisions:read",
    ("POST", "/v1/plan"):                    "plan:opprett",
    ("POST", "/v1/plan/{id:uuid}/aktiver"):   "plan:aktiver",
    ("POST", "/v1/plan/{id:uuid}/gjenoppta"): "plan:gjenoppta",
    ("POST", "/v1/plan/{id:uuid}/stans"):     "plan:opprett",
    ("GET",  "/v1/plan/{id:uuid}/historikk"): "decisions:read",
    ("POST", "/v1/domener"):                 "bestilling:opprett",
    ("POST", "/v1/modul/onboarding"):        "modules:onboard",
    ("POST", "/v1/modul/onboarding/innlos"): "onboarding-hemmelighet",
    ("POST", "/v1/modul/token/roter"):       "modultoken",
    ("POST", "/v1/modul/token/tilbakekall"): "modules:onboard",
    ("POST", "/v1/policyutkast"):            "policy:write",
    ("GET",  "/v1/policyutkast"):            "policy:read",
    ("POST", "/v1/policyutkast/{utkast_id:str}/simuler"): "policy:read",
    ("POST", "/v1/policyutkast/{utkast_id:str}/valider"): "policy:write",
    # INNBOKSEN ER MOTTAKERENS, IKKE POLICYFORVALTNINGENS (Codex P2). Begge
    # POST-ene rører KUN kallerens egne rader — bruker-id-en kommer fra
    # økten, aldri fra kroppen — så `policy:write` var en fullmakt de ikke
    # trenger. Kravet lot seg heller ikke forsvare etter 044: pause- og
    # bruddvarslene går til administratoren som aktiverte planen, og den
    # rollen har verken `policy:write` eller `policy:activate`. Hun kunne
    # altså MOTTA et varsel hun ikke kunne kvittere ut — og hadde hun valgt
    # `kun_portal`, kunne hun ikke engang endre valget tilbake.
    # Å handle på et varsel skal aldri kreve mer enn å se det.
    ("GET",  "/v1/varsel"):                  "policy:read",
    ("POST", "/v1/varsel/{varsel_id:str}/lest"): "policy:read",
    ("POST", "/v1/varselvalg"):              "policy:read",
    ("POST", "/v1/policy/{policy_id:str}/slett"): "policy:write",
    ("POST", "/v1/policyutkast/{utkast_id:str}/forkast"): "policy:write",
    ("POST", "/v1/policyutkast/{utkast_id:str}/gjenapne"): "policy:write",
    ("POST", "/v1/policyutkast/{utkast_id:str}/aktiveringsrunde"): "policy:activate",
    ("POST", "/v1/policyutkast/{utkast_id:str}/attester"): "policy:activate",
    ("GET",  "/v1/policyutkast/{utkast_id:str}"): "policy:read",
    ("PUT",  "/v1/policyutkast/{utkast_id:str}"): "policy:write",
    # PR-010: OIDC-sesjon. /start og /callback er uautentiserte (de
    # ETABLERER sesjonen); /v1/sesjon GET/DELETE gjelder sesjonen selv og
    # scope-gates ikke — de er sesjonshåndtering, ikke lese-data.
    # M-6 PR-B: kildeforvaltningen. Lista er `epost:read` (flatens
    # leseflate); mutasjonene bærer `epost:kilde:administrer` (browser-
    # sesjon + CSRF). Callbacken er en NAVIGASJON fra Microsoft og
    # dermed uautentisert som OIDC-callbacken: credentialet er den
    # MAC-ede engangsstaten + browserbindingen, aldri en sesjon.
    ("GET",  "/v1/epost/kilder"):            "epost:read",
    ("POST", "/v1/epost/kilder/start"):      "epost:kilde:administrer",
    ("GET",  "/v1/epost/kilder/callback"):   None,
    ("POST", "/v1/epost/kilder/{kilde_id:uuid}/deaktiver"):
        "epost:kilde:administrer",
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


def _utled_opplastingskapabilitet(conn, auth, tenant: str,
                                  opp_id: int, ef):
    """Utsteder opplastingskapabiliteten for et claimet oppdrag — delt av
    claim (015/017) og fornyelsen (063/#165). Bindingen er
    SERVERKONTEKSTENS (tenant · oppdrag · modul · release · kontrakt ·
    epoch · artefakttype); modulen ber aldri om felt. Alle
    fail-closed-reglene (tvetydig release/type, test.-prefikset i
    produksjon, evidensfrist-klemmen) bor HER, én gang. -> dict | None.
    Kalles i claimens/fornyelsens egen transaksjon; committer aldri selv.
    """
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
    #
    # Codex (P2): utledningen over er for LEGACY-api-tokener, som
    # ikke bærer noen deployment i det hele tatt. Et modultoken
    # BÆRER sin — release og miljø ble bundet ved onboardingen, og
    # claimen har alt verifisert at nettopp den deploymenten er
    # `claiming` med gjeldende epoch. Da er et oppslag som ikke kan
    # skille staging fra produksjon både unødvendig og feil: er
    # samme kontrakt deployet i BEGGE miljøer, ga det «tvetydig
    # release» (ingen kapabilitet) eller — verre — produksjonssvaret
    # på et staging-token, se `er_produksjon` under.
    if isinstance(auth, ModulAutentisert):
        autentisert_release, autentisert_miljo = (auth.release_id,
                                                  auth.miljo)
    else:
        autentisert_release = autentisert_miljo = None
    if oppdragsrad is not None and oppdragsrad[0] is not None \
            and (autentisert_release is not None
                 or (oppdragsrad[1] is not None
                     and "," not in oppdragsrad[1])):
        (o_modul, o_release, o_kv, o_khash, o_epoch) = oppdragsrad
        if autentisert_release is not None:
            o_release = autentisert_release
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
        # `test.`-prefikset er reservert (035 §8): det utledes
        # ALDRI når DENNE claimen kommer fra produksjon —
        # selvtest-artefakter skal ikke kunne bære kundedata, og en
        # testtype i produksjonskjeden er en konfigurasjonsfeil,
        # ikke en fullmakt. Filteret står i SQL-en så «nøyaktig én
        # type»-regelen teller de typene som faktisk kan utstedes.
        #
        # Codex (P2): porten spør om DEN AUTENTISERTE claimens
        # miljø, ikke om kontrakten finnes i produksjon et sted.
        # Med et modultoken står miljøet i tokenet. Uten et
        # modultoken finnes ingen autentisert deployment å spørre,
        # og da er «finnes den i produksjon» det nærmeste
        # fail-closed svaret — et legacy-token er miljøløst, og å
        # anta staging for det ville vært å gjette den veien som
        # slipper mest ut.
        if autentisert_miljo is not None:
            er_produksjon = autentisert_miljo == "produksjon"
        else:
            er_produksjon = bool(conn.execute(
                "SELECT EXISTS (SELECT 1 FROM moduldeployment dp"
                " WHERE dp.modul_id=%s AND dp.livslop='claiming'"
                "   AND dp.kontraktversjon=%s AND dp.kontrakt_hash=%s"
                "   AND dp.miljo='produksjon')",
                (o_modul, o_kv, o_khash)).fetchone()[0])
        typerader = conn.execute(
            "SELECT artefakttype FROM artefakttype_register"
            " WHERE eiermodul=%s AND kontraktversjon=%s"
            "   AND kontrakt_hash=%s"
            "   AND NOT (artefakttype LIKE 'test.%%' AND %s)"
            " ORDER BY artefakttype LIMIT 2",
            (o_modul, o_kv, o_khash, er_produksjon)).fetchall()
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
            # `utloper = now() + min(igjen, oppdragskontrakt.UTSTEDT_AUTORITET_S) <= evidensfrist`
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
                #
                # Codex P1: kapabiliteten stemples med DEPLOYMENTENS
                # miljø når claimen kom fra et modultoken, så
                # innløsningen kan kreve hele den autentiserte
                # deploymenten (`_artefakt_upload`). Et legacy-token
                # har ingen — da står miljøet NULL, som før.
                orad = conn.execute(
                    "SELECT jti, utloper FROM utsted_artefaktkapabilitet("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tenant, opp_id, o_modul, o_release, o_kv, o_khash,
                     o_epoch, typerad[0], opplasting_jti,
                     min(igjen, oppdragskontrakt.UTSTEDT_AUTORITET_S), autentisert_miljo)).fetchone()
                # Grensen HÅNDHEVES, den forutsettes ikke. Utledningen
                # over gjør `utloper <= evidensfrist` til en identitet,
                # men den identiteten hviler på 017s klemming — og en
                # kapabilitet som overlever evidensen den er til for
                # skal ikke kunne slippe ut fordi et ledd endret seg et
                # annet sted. Går den likevel over, utstedes ingen
                # `opplasting` i svaret: jti-en er da aldri utlevert og
                # kan ikke innløses.
                if orad is not None and orad[1] <= ef:
                    # BESLUTNING-168 §3: hvilken form som promoteres NÅ er
                    # en lagringstilstand, ikke en avtale. Produsenten får
                    # gjeldende skjemaversjon i samme svar som retten til
                    # å laste opp, lest i claim-transaksjonen — vinduet
                    # mellom flipp og opplasting er da nøyaktig
                    # kjøringens lengde, og synlig (opplastingen
                    # revalideres mot gjeldende uansett). En type uten
                    # versjonsrad er pre-072-legacy og bærer v1.
                    vrad = conn.execute(
                        "SELECT skjemaversjon FROM artefakttype_versjon"
                        " WHERE artefakttype=%s AND status='gjeldende'",
                        (typerad[0],)).fetchone()
                    opplasting = {"jti": orad[0],
                                  "utloper": orad[1].isoformat(),
                                  "artefakttype": typerad[0],
                                  "skjemaversjon":
                                      vrad[0] if vrad else 1}

    return opplasting


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

        # LUKKET SKJEMA (035, port 8): claim har INGEN lovlige parametre.
        # En request som sender release/miljø/epoch — eller hva som helst
        # annet — AVVISES, den ignoreres ikke: identiteten kommer fra
        # tokenet, og en klient som prøver å sende den skal få vite at den
        # veien ikke finnes, ikke lures til å tro at den virket.
        raa_kropp = request.scope.get("state", {}).get("kropp", b"")
        if raa_kropp:
            try:
                kropp_data = json.loads(raa_kropp.decode("utf-8"))
            except (ValueError, RecursionError):
                # Codex P2: `json.loads` er REKURSIV. Et syntaktisk gyldig,
                # dypt nøstet dokument på noen få kilobyte (≈2 000 nivåer)
                # ligger godt under kroppsgrensen på 256 KiB og treffer
                # likevel rekursjonsgrensen — RecursionError er en
                # RuntimeError, ikke en ValueError, så `except ValueError`
                # alene slapp den ut som generisk 500 i stedet for det
                # dokumenterte `request_feilformet`. Dybde er klientinput,
                # og denne parseren er ny i 035; onboarding- og
                # artefaktparserne fanger den allerede.
                return _feilsvar("request_feilformet", rid)
            if kropp_data not in ({}, None):
                tjeneste.logg.hendelse("request_feilformet", rid, auth.tenant,
                                       feiltype="claim_med_parametre")
                return _feilsvar("request_feilformet", rid)

        # TOKENET AUTENTISERER, REGISTERET AUTORISERER (035 §2/§6).
        # Token, deployment, status og epoch portes med EKSPLISITTE avslag —
        # «du har ikke lov» og «det finnes ikke arbeid» må aldri se like ut
        # (port 18–19). Porten er ÉN funksjon fordi den leses to ganger —
        # før claimen og etter et tomt resultat — og to kopier av den samme
        # dommen ville drevet fra hverandre.
        def _modulporten():
            """(drad, feilsvar): feilsvar er None når modulen får claime.

            TOKENET REVALIDERES FØRST (Codex P1), og det er ikke en
            omorganisering: fram til nå leste porten bare REGISTERET —
            deployment, modulstatus, epoch — mens tokenraden aldri ble sett
            igjen etter `preauth`, som eier og LUKKER sin egen transaksjon.
            `claim_neste_oppdrag` kunne ikke ta den heller: den får ingen
            token-id og har ingenting å slå opp `tilbakekalt_ts` på. En
            eksplisitt tilbakekalling som committer mellom `preauth` og
            claimen traff derfor ingen port i det hele tatt, og det
            tilbakekalte tokenet fikk tildelt nytt arbeid — stikk i strid
            med at endepunktet lover ØYEBLIKKELIG virkning.

            Revalideringen er den SAMME funksjonen de to
            innløsningsveiene bruker, og det er hele poenget: dommen «er
            denne deploymenten fortsatt autorisert?» skal være én regel, én
            gang, ikke en kopi per dør. Den tar den delte modul-låsen, og
            låsen er transaksjonsbundet — den holdes altså HELE veien fram
            til `claim_neste_oppdrag` har tildelt. Et nødstopp eller en
            tilbakekalling kan ikke gli inn mellom dommen og tildelingen.

            Modulstatus og epoch leses derfor ikke lenger her: de var de
            samme to sjekkene, ULÅST, og etter revalideringen kan de per
            konstruksjon ikke fyre — de ville stått som død kode som ser ut
            som en port. Igjen står det som er DEPLOYMENTENS eget og som
            revalideringen med vilje ikke ser: livsløpet (en `draining`
            deployment skal ikke få NYTT arbeid, men skal få levere det den
            har) og kontraktfeltene autorisasjonen utledes fra.
            """
            revalidering = _modultoken_revalidert(tjeneste, conn, auth, rid)
            if revalidering is not None:
                return None, revalidering
            drad = conn.execute(
                "SELECT d.livslop, d.kontraktversjon, d.kontrakt_hash"
                "  FROM moduldeployment d"
                " WHERE d.modul_id=%s AND d.miljo=%s AND d.release_id=%s",
                (auth.modul_id, auth.miljo, auth.release_id)).fetchone()
            if drad is None or drad[0] != "claiming":
                conn.rollback()
                tjeneste.logg.hendelse("modul_ikke_claimbar", rid,
                                       auth.tenant,
                                       livslop=drad[0] if drad else "borte")
                return None, _feilsvar("modul_ikke_claimbar", rid)
            return drad, None

        claim_release = claim_miljo = claim_epoch = None
        if isinstance(auth, ModulAutentisert):
            drad, portsvar = _modulporten()
            if portsvar is not None:
                return portsvar
            # Autorisasjonen utledes ved BRUK, via releasens kontrakt: raden
            # må matche eiermodul OG begge kontraktfeltene (positiv
            # tillatelsesliste). En type registrert under en ANNEN kontrakt
            # bidrar med ingenting — parallelle kontrakter holder seg fra
            # hverandre begge veier (port 33–34). Typenavnet oversettes til
            # handlingsprefikser gjennom den LUKKEDE typeregistreringen i
            # `oppdragskontrakt`; en registerrad uten kodefestet type
            # bidrar med ingenting (fail-closed).
            typerader = conn.execute(
                "SELECT oppdragstype FROM oppdragstype_register"
                " WHERE eiermodul=%s AND kontraktversjon=%s"
                "   AND kontrakt_hash=%s",
                (auth.modul_id, drad[1], drad[2])).fetchall()
            prefikser = sorted({
                pre for (typenavn,) in typerader
                for pre in getattr(
                    oppdragskontrakt.OPPDRAGSTYPER.get(typenavn),
                    "handlingsprefikser", ())})
            if not prefikser:
                conn.rollback()
                tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                       scope="utledet:ordre")
                return _feilsvar("scope_mangler", rid)
            claim_release, claim_miljo = auth.release_id, auth.miljo
            claim_epoch = auth.utstedt_epoch
        else:
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
            #
            # 300 er et GULV, ikke leasen (Codex P1 → migrasjon 037). Endepunktet
            # vet ikke hvilket oppdrag det er i ferd med å dele ut — hvor lenge
            # arbeidet får ta står på RADEN (`utforelsesfrist`), og bare
            # `claim_neste_oppdrag` har den når leasen settes. Funksjonen
            # strekker derfor leasen til minst den fristen, opp til sitt eget
            # tak på 3600 s. Et fast tall her ville betydd at et langt oppdrag
            # (WCAG: 30/60 min) fikk leasen sin til å utløpe MENS utføreren
            # jobbet, og en annen utfører ville reclaimet det og bestilt det
            # samme eksterne arbeidet en gang til.
            rad = conn.execute(
                "SELECT id, tenant, unntak_id, oppdragstype, handling,"
                " repair_operation_id, payload_kryptert, key_id, nonce,"
                " owner_generation, utforelsesfrist, evidensfrist"
                "  FROM claim_neste_oppdrag(%s, %s, %s, %s, %s, %s, %s)",
                (auth.rolle, prefikser, claim_id, 300, claim_release,
                 claim_miljo, claim_epoch)).fetchone()
            if rad is None:
                conn.rollback()
                # Codex P2: TOM KØ ER EN PÅSTAND OM ARBEID, IKKE OM TILLATELSE.
                #
                # Pre-porten over leses UTEN modul-låsen. Rekker
                # `noddeaktiver_modul`, en drenering eller et epoch-bytte å
                # committe etter den lesningen, men før claim_neste_oppdrag
                # får låsen, forkaster SQL-funksjonen med rette hver kandidat
                # og returnerer ingen rad. Da er 204 en LØGN: modulen mistet
                # lov til å claime, og fikk beskjed om at det ikke fantes
                # arbeid — nøyaktig sammenblandingen port 18–19 forbyr, og
                # den som får en nøddeaktivert modul til å polle videre i
                # stedet for å stanse. Rollbacken over avsluttet
                # transaksjonen, så porten leses her på nytt og ser det som
                # faktisk er committet. Holder autorisasjonen fortsatt, var
                # køen virkelig tom.
                if isinstance(auth, ModulAutentisert):
                    _, portsvar = _modulporten()
                    if portsvar is not None:
                        return portsvar
                    conn.rollback()
                # EKTE 204 — UTEN KROPP (eiers logfunn 30/8). En 204 med
                # JSON-kropp får h11 til å drepe forbindelsen på «Too
                # much data for declared Content-Length»: hver eneste
                # tomme claim-poll (hvert 2.–10. sekund, hele døgnet)
                # etterlot en full traceback i journald og et avkuttet
                # svar hos arbeideren — som bare måler statuskoden
                # (controller.py:489) og aldri leste kroppen. 204 BETYR
                # ingen kropp; nå er den det.
                return Response(status_code=204,
                                headers={"x-request-id": rid})

            (opp_id, tenant, unntak_id, oppdragstype, handling, repair_id,
             ct, key_id, nonce, owner_gen, uf, ef) = rad

            # Dekrypteringen krever tenantkontekst — samme port som alle
            # andre veier inn. Konteksten settes ETTER claimen fordi
            # oppdragskøen er på tvers av tenanter (modulen betjener mange),
            # og tenanten er ukjent til claimen har skjedd.
            sett_kontekst(conn, tenant, auth.aktor, rid)
            # 038 §5: opphavet er metadata i svaret, ikke autorisasjon —
            # modulen gjør det samme arbeidet uansett. Leses etter claimen
            # (claim_neste_oppdrag-signaturen er 008s og røres ikke).
            # `owner_lease_utloper` leses fra SAMME rad claimen nettopp
            # skrev (#219): horisonten er serverens, og dette er den ENE
            # kilden — å regne den ut i klienten ville speilet 037s
            # formel, to sannheter som driver fra hverandre ved neste
            # migrasjon. Fornyelsessvaret bærer alt feltet (063); nå gjør
            # claim-svaret det også, så heartbeatets teller-tilbakefall
            # før første fornyelse dør.
            opprinnelse, lease_utloper = conn.execute(
                "SELECT opprinnelse, owner_lease_utloper FROM oppdrag"
                " WHERE tenant=%s AND id=%s",
                (tenant, opp_id)).fetchone()
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
            # RETENSJONSANKERET FØDES I CLAIM-TRANSAKSJONEN (Codex P1,
            # #220). 057-kontrakten sier at kandidatprosessen fødes mens
            # oppdraget er aktivt claimet, og claim-rollen bærer
            # INSERT-grantet nettopp for dette — men ingen kalte døren,
            # så lesegrensens reap-predikat var vakuøst sant for alltid:
            # rapporten kunne aldri bli reap-bar. Døren er idempotent
            # (samme oppdrag ⇒ samme prosess-id), så re-claim etter tapt
            # lease er trygt. Fristen er kundens valg fra det signerte
            # oppdraget; fraværet ER standardvalget (basens DEFAULT 90).
            # Feiler fødselen, finnes ingen claim — et claimet oppdrag
            # uten retensjonsanker er nøyaktig tilstanden Codex målte.
            if oppdragstype == "rekruttering.evaluering":
                frist = (minimert or {}).get("slettefrist_dogn")
                try:
                    if frist is None:
                        conn.execute(
                            "SELECT opprett_rekrutteringsprosess(%s,%s)",
                            (tenant, opp_id))
                    else:
                        conn.execute(
                            "SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                            (tenant, opp_id, frist))
                except psycopg.Error:
                    conn.rollback()
                    tjeneste.logg.hendelse("intern_feil", rid, tenant,
                                           art="drift",
                                           oppdrag=str(opp_id))
                    return _feilsvar("intern_feil", rid)

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

            # Codex P1: kapabiliteten stempler DEPLOYMENTEN som claimet, ikke
            # bare modulen. `modul_id` er delt mellom alle levende
            # deployments av modulen, og kvitteringsveien slipper dem alle
            # forbi scope-porten (retten er kapabilitetens) — uten miljø og
            # release å sammenligne mot kunne en staging-deployment, eller en
            # utgått release med et fortsatt levende token, levere resultatet
            # for produksjonsdeploymentens claim og avslutte den jobben.
            # `claim_miljo`/`claim_release` er tokenets, ikke noe kalleren
            # oppgir; med et legacy-api-token er de NULL, og kapabiliteten
            # blir deploymentløs (og kan da bare innløses av en like
            # deploymentløs credential).
            kvittering_jti = secrets.token_hex(16)
            kap = conn.execute(
                "SELECT jti, utloper FROM utsted_kvitteringskapabilitet("
                "%s,%s,%s,%s,%s,%s)",
                (opp_id, claim_id, owner_gen, kvittering_jti,
                 claim_miljo, claim_release)).fetchone()
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
            # Utledningen er DELT med fornyelsesveien (#165): nøyaktig
            # samme serverkontekst-binding, samme fail-closed-regler —
            # se `_utled_opplastingskapabilitet`.
            opplasting = _utled_opplastingskapabilitet(
                conn, auth, tenant, opp_id, ef)
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
            # 038 §5: `unntak_id` er null for beslutningsoppdrag — saken
            # peker på oppdraget, aldri omvendt (port 27/28).
            "opprinnelse": opprinnelse,
            "oppdragstype": oppdragstype, "handling": handling,
            "repair_operation_id": repair_id, "owner_claim_id": claim_id,
            "owner_generation": owner_gen,
            # Horisonten 037 skrev i claim-UPDATE-en (#219) — samme felt
            # fornyelsen returnerer, fra samme kolonne.
            "owner_lease_utloper": lease_utloper.isoformat(),
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


#: Dør-eid navnerom for kandidatlagrenes deterministiske identiteter
#: (#173, eiers valg b): plattformen utleder UUID-ene av (tenant,
#: prosess, manifest-id[, dokumentnavn]) — én kilde, modulen ser dem
#: aldri. #157 kan løfte utledningen til en ankertabell uten at flaten
#: endres. Separatoren er husets (`\x1f`, jf. buntlåsen).
_KANDIDAT_NS = uuid.uuid5(uuid.NAMESPACE_URL, "disponit:m57:kandidatlager")
#: Manifestets kandidat-ID-form — KONTRAKT (kontrakt/KONTRAKT.md, #216
#: valg A). Speilet av modulens `parsing.KANDIDAT_ID_KANON`; kilden er
#: kontrakten, og api/ importerer aldri modulkode.
_KANDIDAT_ID_KANON = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
#: Arkivmedlemmets navn: ZIPs `file name length` er 16-bits, så 65 535
#: byte er det lengste navnet et medlem overhodet kan bære. Grensen er
#: ARKIVKONTRAKTENS, ikke dørens egen — se `_kandidatdata`.
#:
#: MÅLT I TEGN, IKKE I UTF-8-BYTE (Codex P2). Døren fikk aldri arkivets
#: navnefelt; den får `dokumentnavn` som en DEKODET streng, og
#: `zipfile` dekoder med arkivets egen koding (CP437 når UTF-8-flagget
#: mangler). Å re-kode den til UTF-8 og måle DE bytene måler en koding
#: arkivet aldri brukte: et lovlig legacy-navn med 40 000 CP437-`é` er
#: 40 000 byte i arkivet og 80 000 i UTF-8, så `parsing.inspiser_bunt`
#: godtok bunten mens denne porten svarte `request_feilformet` — og
#: `lagre_dokument` reiser den som `kandidatlagring_feilet` for hele
#: evalueringen. Tegn er derimot den ene målingen som ALDRI kan avvise
#: et navn arkivet kan bære: enhver koding bruker minst én byte per
#: tegn, så et navn på ≤ 65 535 byte i arkivet er ≤ 65 535 tegn dekodet.
#: Fortsatt en grense — `dokumentnavn` går inn i uuid5, i en
#: TEXT-kolonne og i loggens detalj — men nå formatets egen.
_KANDIDAT_NAVN_MAKS = 65_535
#: De tre lovede innholdstypene — endelse -> MIME. Alt annet er alt
#: felt av arkivgaten; her er det en feilformet forespørsel.
_KANDIDAT_MIME = {
    ".pdf": "application/pdf",
    ".docx": ("application/vnd.openxmlformats-officedocument"
              ".wordprocessingml.document"),
    ".html": "text/html", ".htm": "text/html"}


def _har_ulagringsbart_tegn(rot) -> bool:
    """Bærer noen streng i strukturen et tegn raden ikke kan ta imot —
    nøkler medregnet? To klasser, ÉN gjennomgang.

    LØSREVNE SURROGATER (Codex P2, #173). `json.loads` gjør escapen
    `\\ud800` til et ekte lone surrogate i Python-strengen, og en slik
    streng er IKKE en gyldig Unicode-scalar-sekvens: `str.encode("utf-8")`
    reiser `UnicodeEncodeError`. Den er verken `psycopg.Error` eller noe
    porten under fanger, så den falt ut som en UKODET 500 — på hvert
    eneste retryforsøk, siden samme kropp gir samme unntak — og felte
    evalueringen uten å si hva som var galt. Manifestpredikatene slipper
    verdien gjennom fordi et søskeninnslag matcher, og `avmaskering`
    beholder hver deklarert verdi, så veien hit er helt vanlig.

    Den treffer BEGGE grenene: artefaktveien på `r.encode("utf-8")` i
    størrelsesporten, dokumentveien på `uuid.uuid5`, som encoder navnet
    sitt. Derfor måles den her, i det ene predikatet begge grenene
    allerede spør, og FØR noe forsøkes kodet.

    Nullbyten er den andre klassen, og den var her først:

    MÅLT PÅ VERDIENE, IKKE PÅ JSON-TEKSTEN (Codex P2). Porten sto som
    `"\\x00" in raa`, altså på den kanoniske JSON-strengen — og der
    finnes nullbyten ALDRI: `json.dumps` skriver den som de seks tegnene
    `\\u0000`, uansett `ensure_ascii`. Predikatet var dermed dødt, og en
    nullbyte i en nestet verdi (manifestkontrakten tillater f.eks. en
    ikke-matchende alternativverdi for et personfelt, som når
    `avmaskering` uten å bli sett) nådde `jsonb`, som avviste den. Rå
    `psycopg.Error` → handlerens catch-all → `db_utilgjengelig`, altså
    en falsk infrastrukturalarm som modulen retryer mot en frisk base
    før den feller evalueringen. Nøyaktig utfallet den opprinnelige
    fiksen fantes for å hindre.

    MÅLT PÅ VERDIENE, IKKE PÅ JSON-TEKSTEN (Codex P2). Porten sto som
    `"\\x00" in raa`, altså på den kanoniske JSON-strengen — og der
    finnes nullbyten ALDRI: `json.dumps` skriver den som de seks tegnene
    `\\u0000`, uansett `ensure_ascii`. Predikatet var dermed dødt, og en
    nullbyte i en nestet verdi (manifestkontrakten tillater f.eks. en
    ikke-matchende alternativverdi for et personfelt, som når
    `avmaskering` uten å bli sett) nådde `jsonb`, som avviste den. Rå
    `psycopg.Error` → handlerens catch-all → `db_utilgjengelig`, altså
    en falsk infrastrukturalarm som modulen retryer mot en frisk base
    før den feller evalueringen. Nøyaktig utfallet den opprinnelige
    fiksen fantes for å hindre.

    Å lete etter escapen `\\u0000` i JSON-teksten i stedet ville vært
    feil andre vei: en tekst som LOVLIG inneholder de seks tegnene
    `\\u0000` blir dumpet som `\\\\u0000`, som inneholder søkestrengen —
    en gyldig kandidat avvist på en nullbyte den ikke har.

    Iterativ og ikke rekursiv med vilje: dybden er kallerens, og en
    `RecursionError` her ville vært en 500 der porten skal svare
    `request_feilformet`.
    """
    stakk = [rot]
    while stakk:
        verdi = stakk.pop()
        if isinstance(verdi, str):
            if "\x00" in verdi:
                return True
            # Surrogatet måles ved å FORSØKE kodingen, ikke ved å lete
            # etter kodepunkter i U+D800–U+DFFF for hånd: `encode` ER
            # regelen nedstrøms, og et eget intervallsøk ville vært en
            # andre sannhet om samme spørsmål (§9 K4 — ekte koder, ikke
            # en etterligning av den).
            try:
                verdi.encode("utf-8")
            except UnicodeEncodeError:
                return True
        # IKKE-ENDELIGE TALL ER SAMME KLASSE (#260 P2-1, Codex på #259):
        # `json.loads` er permissiv og tar imot `NaN`/`Infinity`, og
        # `json.dumps` skriver de samme ikke-standard-tokenene ut igjen
        # — men `jsonb` avviser dem, og da er kjeden nøyaktig nullbytens:
        # rå psycopg.Error → db_utilgjengelig → falsk driftsalarm og en
        # brent retrykjede mot en frisk base. Verdien raden ikke kan ta
        # imot måles HER, i den ene gjennomgangen begge veiene alt spør.
        # `bool` er en int-subklasse og aldri float, så armen er ren.
        elif isinstance(verdi, float) and not math.isfinite(verdi):
            return True
        elif isinstance(verdi, dict):
            stakk.extend(verdi.keys())
            stakk.extend(verdi.values())
        elif isinstance(verdi, list):
            stakk.extend(verdi)
    return False


def _kandidatdata(tjeneste: Tjeneste, request: Request, form: str) -> Response:
    """Skriveveien inn i kandidatlagrene (#173, eiers valg b + i).

    057 designet denne døren («Runtime skriver lagrene gjennom
    API-veien») — dette er den. Autentiseringen er kvitteringens og
    fornyelsens form: modultokenet svarer på hvilken deployment dette
    er, og FULLMAKTEN ER CLAIMETS — kroppen bærer (tenant, oppdrag_id,
    owner_claim_id, owner_generation), og raden må matche et aktivt
    claimet `rekruttering.evaluering`-oppdrag hos denne modulen med
    levende lease OG levende retensjonsanker. Tenant er kallerens
    påstand bare som RLS-nøkkel: claim-paret er hemmeligheten som
    binder, og et feil tenantvalg finner ingen rad.

    `form="dokument"`: originaldokument + parsettekst i SAMME
    transaksjon (FK-kjeden er kontrakten — eiers valg i). `form=
    "kandidat"`: evalueringsartefakt + ev. intervjuspørsmål.

    IDEMPOTENT PÅ PAYLOAD-LIKHET: lagrene er append-only, og en retry
    etter tapt lease skriver de samme bytene — det er et stille ja. Et
    AVVIKENDE re-skriv under samme nøkkel er to sannheter om samme
    dokument og felles som `kandidatdata_konflikt`. Alle
    autorisasjonsutfall er ETT svar (`kandidatdata_avvist`, 058-formen).
    Lagervaktene (057) står bak døren og måler det samme for enhver
    rolle — denne døren kan aldri være lagrenes eneste vern.
    """
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        auth = preauth(tjeneste, conn, request.headers.get("authorization"),
                       rid)
        if auth is None or auth.kapabilitet is not None:
            tjeneste.logg.hendelse("token_ugyldig", rid)
            return _feilsvar("token_ugyldig", rid)
        if not isinstance(auth, ModulAutentisert) and not _modulscope(auth):
            tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                   scope=ORDRESCOPE + "<prefiks>")
            return _feilsvar("scope_mangler", rid)
        # EGEN BØTTE, IKKE MODULTOKENETS DELTE (Codex P1). Linjen sto som
        # «samme ratebudsjett som claim/forny/upload», og det gjorde
        # plattformens egen grense til en port mot dens egne dokumenterte
        # maksima: en bunt kan lovlig bære 20 000 filer og 5 000
        # kandidater, altså 25 000 skrivinger, mens standardbudsjettet er
        # 12 000 per rullende minutt. Strømmer uttrekket mer enn 12 000
        # små dokumenter innenfor et minutt, får neste skriving 429 —
        # `lever` leser 4xx som terminalt, og `kjor_bunt` feller HELE
        # evalueringen med `kandidatlagring_feilet`. Grensen felte altså
        # ikke misbruk; den felte den eneste kjøringen den fantes for.
        #
        # Nøkkelen er EGEN, ikke bare taket: en skrivesløyfe skal hverken
        # sulte modulens claim/forny/kvittering eller sultes av dem. Ett
        # bøttested fortsatt (`Rategrense`), to bøtter.
        if not tjeneste.rate.slipp_gjennom("kandidatdata:" + auth.token_id,
                                           tak=KANDIDATDATA_RATE_PER_MIN):
            tjeneste.logg.hendelse("rate_grense", rid, auth.tenant)
            return _feilsvar("rate_grense", rid)
        if isinstance(auth, ModulAutentisert):
            revalidering = _modultoken_revalidert(tjeneste, conn, auth, rid)
            if revalidering is not None:
                return revalidering

        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            kropp = json.loads(raa.decode("utf-8"))
        except Exception:
            kropp = None
        if not isinstance(kropp, dict):
            tjeneste.logg.hendelse("request_feilformet", rid)
            return _feilsvar("request_feilformet", rid)
        tenant = kropp.get("tenant")
        opp_id = kropp.get("oppdrag_id")
        claim_id = kropp.get("owner_claim_id")
        generasjon = kropp.get("owner_generation")
        kid = kropp.get("kandidat_id")
        if not isinstance(tenant, str) or not tenant \
                or not isinstance(opp_id, int) or isinstance(opp_id, bool) \
                or not isinstance(claim_id, str) or not claim_id \
                or not isinstance(generasjon, int) \
                or isinstance(generasjon, bool) \
                or not isinstance(kid, str) \
                or not _KANDIDAT_ID_KANON.fullmatch(kid) \
                or _har_ulagringsbart_tegn(tenant) \
                or _har_ulagringsbart_tegn(claim_id):
            # KONVOLUTTEN MÅLES OGSÅ (#260 P2-2, Codex på #259):
            # `tenant`/`owner_claim_id` gikk bare gjennom typesjekk, så
            # et løsrevet surrogat nådde `sett_kontekst`/claim-oppslaget
            # og reiste `UnicodeEncodeError` — hverken psycopg.Error
            # eller noe porten fanger: en UKODET 500, på hvert retry.
            # (`kandidat_id` bæres av _KANDIDAT_ID_KANON alene.)
            tjeneste.logg.hendelse("request_feilformet", rid)
            return _feilsvar("request_feilformet", rid)

        modul = auth.modul_id if isinstance(auth, ModulAutentisert) \
            else auth.rolle
        # DEPLOYMENTEN, IKKE BARE MODULEN (Codex P1). Bindingen målte
        # `o.eiermodul`, og `modul_id` er DELT av hver levende deployment
        # av modulen: staging og produksjon, gammel release og ny, svarer
        # alle det samme på det spørsmålet. Et claim-trippel som lekker
        # eller replayes til en annen deployment av samme modul kunne
        # derfor skrive persondata inn i en annen deployments prosess —
        # og siden lagrene er append-only, ville den LOVLIGE utføreren
        # etterpå møtt `kandidatdata_konflikt` på sin egen kandidat og
        # felt hele evalueringen. Claim-paret er en hemmelighet, men det
        # er ikke deploymentens identitet, og døren skal måle begge.
        #
        # Formen er `hent_inndata_for_oppdrag` sin (060:102–103):
        # `claim_release_id`/`claim_miljo` er sporet claim-porten selv
        # STEMPLET (049 §0) fra TOKENET — aldri noe kalleren oppgir — så
        # det finnes ingen vei til å påstå seg til en annen deployment.
        #
        # `IS NOT DISTINCT FROM`, ikke `=`: et legacy-api-token claimer
        # deploymentløst, og da står begge kolonnene NULL (`app.py:2025`,
        # samme grunn kvitteringskapabiliteten blir deploymentløs og bare
        # kan innløses av en like deploymentløs credential). Med `=`
        # hadde NULL-siden svart UKJENT, og den lovlige legacy-veien inn
        # i lagrene ville stengt seg selv; med denne formen matcher
        # deploymentløs KUN deploymentløs, og en deployment KUN seg selv.
        claim_release = auth.release_id \
            if isinstance(auth, ModulAutentisert) else None
        claim_miljo = auth.miljo if isinstance(auth, ModulAutentisert) \
            else None
        sett_kontekst(conn, tenant, auth.aktor, rid)
        # LÅST LESNING (Cursor P2-3). Uten radlåsen var autorisasjonen et
        # SNAPSHOT: under READ COMMITTED kunne en ny claimer committe et
        # `UPDATE oppdrag SET owner_claim_id/owner_generation/lease` i
        # vinduet mellom dette oppslaget og INSERT-ene under, og denne
        # forespørselen skrev likevel — INSERT-ene måler ikke claimet på
        # nytt, og lagervakten (057) måler `slettet_ts`, ikke leasen. En
        # utfører som HADDE mistet oppdraget skrev da persondata inn i
        # prosessen på vegne av en fullmakt som var borte.
        #
        # `FOR SHARE`, ikke `FOR UPDATE`: claim-tyven og `forny_oppdragslease`
        # tar `FOR UPDATE`, så delelåsen serialiserer mot NØYAKTIG dem —
        # mens den lovlige strømmen av dokument- og artefaktskriv under
        # SAMME claim går videre parallelt. En eksklusiv lås her ville
        # gjort hele skriveveien til en kø på én rad. Og PostgreSQL
        # re-evaluerer predikatet etter låsen: en rad som ble stjålet
        # under ventingen faller ut av treffet i stedet for å bli lest fra
        # et gammelt snapshot — samme mekanikk 057s fødselsvakt bruker.
        #
        # `OF o` er ikke stil, det er en RETTIGHET: enhver radlåsklausul
        # krever UPDATE på tabellen, og runtime har SELECT+UPDATE på
        # `oppdrag` (`deploy/staging/migrer.py`) men KUN SELECT på
        # `rekrutteringsprosess`. En bar `FOR SHARE` her ville forsøkt å
        # låse begge og svart `permission denied` — nøyaktig grunnen
        # `_anker_lever` forkastet delelåsen på leseveien.
        #
        # OG `clock_timestamp()`, IKKE `now()` (Codex P2). Leddet spør
        # «lever holdet NÅ», og `now()` er ikke nå: den er fastfrosset
        # ved transaksjonens START. Denne transaksjonen begynner FØR
        # base64-dekodingen av inntil 25 MiB og før `FOR SHARE` har
        # ventet ut en samtidig claimer — den kan derfor stå åpen
        # vilkårlig lenge mens `now()` peker på tiden før ventingen. En
        # lease som døde i nettopp det vinduet ble da autorisert, og
        # persondata committet på en fullmakt reaperen alt hadde
        # inndratt. `clock_timestamp()` leses på nytt ved evalueringen
        # og måler den faktiske skrivetiden. Retningen er trygg: den er
        # alltid ≥ `now()`, så porten kan bare bli STRENGERE — den
        # slipper aldri gjennom noe reclaimeren (som selv måler med
        # `now()`, 005:894-895) alt har tatt. Samme ledd og samme grunn
        # som `hent_inndata_for_oppdrag` (060:66-78, 101).
        rad = conn.execute(
            "SELECT p.prosess_id FROM oppdrag o"
            "  JOIN rekrutteringsprosess p"
            "    ON p.tenant = o.tenant AND p.oppdrag_id = o.id"
            " WHERE o.tenant=%s AND o.id=%s AND o.eiermodul=%s"
            "   AND o.oppdragstype='rekruttering.evaluering'"
            "   AND o.status='plukket' AND o.owner_claim_id=%s"
            "   AND o.owner_generation=%s"
            "   AND o.owner_lease_utloper IS NOT NULL"
            "   AND o.owner_lease_utloper > clock_timestamp()"
            "   AND o.claim_release_id IS NOT DISTINCT FROM %s"
            "   AND o.claim_miljo IS NOT DISTINCT FROM %s"
            "   AND p.slettet_ts IS NULL"
            " FOR SHARE OF o",
            (tenant, opp_id, modul, claim_id, generasjon,
             claim_release, claim_miljo)).fetchone()
        if rad is None:
            conn.rollback()
            tjeneste.logg.hendelse("kandidatdata_avvist", rid, tenant,
                                   art="sikkerhet", oppdrag_id=opp_id)
            return _feilsvar("kandidatdata_avvist", rid)
        prosess_id = rad[0]
        kid_uuid = uuid.uuid5(
            _KANDIDAT_NS, f"{tenant}\x1f{prosess_id}\x1f{kid}")
        # 075 (#157): ankeret fødes FØRST, gjennom døren — idempotent,
        # med samme FOR SHARE-serialisering mot reaperen som lagervaktene.
        # Lagrene FK-er ankeret, så en skrivefeil i kandidat-id-en er
        # ikke lenger en ny, lovlig kandidat med ett lager.
        try:
            conn.execute("SELECT opprett_kandidat(%s,%s,%s)",
                         (tenant, prosess_id, kid_uuid))
        except psycopg.errors.InsufficientPrivilege:
            # Dørens egen dom (reapet/usynlig prosess) er en AVVISNING av
            # skrivet — samme kode som lagervaktens, aldri en driftsfeil.
            conn.rollback()
            tjeneste.logg.hendelse("kandidatdata_avvist", rid, tenant,
                                   art="sikkerhet", oppdrag_id=opp_id,
                                   detalj="ankerfodsel")
            return _feilsvar("kandidatdata_avvist", rid)

        def _konflikt(detalj):
            conn.rollback()
            tjeneste.logg.hendelse("kandidatdata_konflikt", rid, tenant,
                                   art="sikkerhet", oppdrag_id=opp_id,
                                   detalj=detalj)
            return _feilsvar("kandidatdata_konflikt", rid)

        svar = {"kandidat_id": str(kid_uuid), "request_id": rid}
        if form == "dokument":
            navn = kropp.get("dokumentnavn")
            b64 = kropp.get("dokument_b64")
            tekst = kropp.get("tekst")
            endelse = ("." + navn.rsplit(".", 1)[-1].lower()
                       if isinstance(navn, str) and "." in navn else "")
            # NAVNEGRENSEN ER ARKIVETS, IKKE DØRENS EGEN (Codex P2). Her
            # sto `len(navn) > 512`, et tall arkivgaten ikke kjenner:
            # `parsing._sjekk_navn` måler traversering og endelse, aldri
            # lengde, og `les_porsjonsvis` strømmer medlemmene uten å
            # materialisere navnene som filsystemstier. En ellers gyldig
            # pdf/docx/html med et lengre ZIP-navn passerte altså hele
            # veien fram hit og fikk `request_feilformet` — som
            # controlleren gjør om til `kandidatlagring_feilet` for HELE
            # evalueringen. Døren avviste en bunt arkivkontrakten godtar.
            #
            # Grensen er derfor formatets egen: ZIPs `file name length`
            # er 16-bits, så 65 535 byte er det lengste navnet et medlem
            # overhodet KAN bære.
            #
            # MÅLT I TEGN (Codex P2, runde 2). Linjen sto som
            # `len(navn.encode("utf-8"))`, altså i en koding arkivet
            # aldri brukte: døren får det DEKODEDE navnet, og et lovlig
            # CP437-navn dobler seg i UTF-8. Se `_KANDIDAT_NAVN_MAKS`
            # for hvorfor tegn er den målingen som ikke kan felle noe
            # arkivet godtar.
            #
            # NUL ER EN FEILFORMET FORESPØRSEL, IKKE EN DØD BASE (Codex
            # P2). PostgreSQL kan ikke lagre en nullbyte i en `TEXT`-verdi
            # i det hele tatt, og et uttrekk fra html eller pdf kan bære
            # en: den passerer arkivgaten og uttrekket, og felte først
            # her — som en rå `psycopg.Error`, som handlerens catch-all
            # oversetter til `db_utilgjengelig`. Modulen leser 5xx som
            # DRIFT, brenner hele retrykjeden mot en base som er frisk,
            # og feller til slutt evalueringen som
            # `kandidatlagring_feilet` — med en falsk infrastrukturalarm
            # på veien, altså feil kø og feil diagnose. Koden skal si
            # hva som faktisk er galt: kroppen bærer et tegn lageret ikke
            # har. Målt på `tekst` OG `navn`, for begge går i
            # TEXT-kolonner (og `navn` også i uuid5 og i loggens detalj).
            #
            # OG SAMME PORT FOR LØSREVNE SURROGATER (Codex P2, #173).
            # Funnet ble meldt på artefaktveien, men dokumentveien har
            # nøyaktig samme defekt én gren unna: `uuid.uuid5` encoder
            # navnet sitt til UTF-8, og `tekst` encodes både til
            # størrelsesmålingen og til sha256. Et `\ud800` fra
            # `json.loads` reiser `UnicodeEncodeError` alle tre stedene —
            # ukodet 500, ikke `request_feilformet`. Å lukke bare den ene
            # grenen ville vært å fikse symptomet: predikatet er derfor
            # det samme her, og det står FØR første koding.
            if not isinstance(navn, str) or not navn \
                    or len(navn) > _KANDIDAT_NAVN_MAKS \
                    or endelse not in _KANDIDAT_MIME \
                    or not isinstance(b64, str) \
                    or not isinstance(tekst, str) \
                    or _har_ulagringsbart_tegn(navn) \
                    or _har_ulagringsbart_tegn(tekst):
                conn.rollback()
                tjeneste.logg.hendelse("request_feilformet", rid, tenant)
                return _feilsvar("request_feilformet", rid)
            try:
                data = base64.b64decode(b64, validate=True)
            except Exception:
                conn.rollback()
                tjeneste.logg.hendelse("request_feilformet", rid, tenant)
                return _feilsvar("request_feilformet", rid)
            # Teksten måles for seg (CodeRabbit, korrigert form): den
            # kan LOVLIG være større enn dokumentbytene — en docx er
            # komprimert, teksten er utpakket — så «tekst ≤ dokument» er
            # feil grense. Taket er §4-tallets egen klasse: én fils
            # budsjett, målt i UTF-8-byte.
            if not data or len(data) > _KANDIDAT_DOK_MAKS \
                    or len(tekst.encode("utf-8")) > _KANDIDAT_DOK_MAKS:
                conn.rollback()
                tjeneste.logg.hendelse("request_feilformet", rid, tenant)
                return _feilsvar("request_feilformet", rid)
            dok_uuid = uuid.uuid5(
                _KANDIDAT_NS,
                f"{tenant}\x1f{prosess_id}\x1f{kid}\x1f{navn}")
            sha = hashlib.sha256(data).hexdigest()
            satt = conn.execute(
                "INSERT INTO kandidat_originaldokument (tenant,"
                " prosess_id, kandidat_id, dokument_id, filnavn,"
                " innholdstype, dokument, storrelse_bytes,"
                " innhold_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT DO NOTHING",
                (tenant, prosess_id, kid_uuid, dok_uuid, navn,
                 _KANDIDAT_MIME[endelse], data, len(data),
                 sha)).rowcount
            if not satt:
                likt = conn.execute(
                    "SELECT dokument = %s AND filnavn = %s"
                    " FROM kandidat_originaldokument"
                    " WHERE tenant=%s AND prosess_id=%s"
                    "   AND kandidat_id=%s AND dokument_id=%s",
                    (data, navn, tenant, prosess_id, kid_uuid,
                     dok_uuid)).fetchone()
                if likt is None or not likt[0]:
                    return _konflikt("originaldokument")
            satt = conn.execute(
                "INSERT INTO kandidat_parsettekst (tenant, prosess_id,"
                " kandidat_id, dokument_id, tekst, innhold_sha256)"
                " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (tenant, prosess_id, kid_uuid, dok_uuid, tekst,
                 hashlib.sha256(tekst.encode("utf-8")).hexdigest()
                 )).rowcount
            if not satt:
                likt = conn.execute(
                    "SELECT tekst = %s FROM kandidat_parsettekst"
                    " WHERE tenant=%s AND prosess_id=%s"
                    "   AND kandidat_id=%s AND dokument_id=%s",
                    (tekst, tenant, prosess_id, kid_uuid,
                     dok_uuid)).fetchone()
                if likt is None or not likt[0]:
                    return _konflikt("parsettekst")
            svar["dokument_id"] = str(dok_uuid)
        else:
            artefakt = kropp.get("artefakt")
            sporsmal = kropp.get("intervjusporsmal")
            # AVMASKERINGEN ER OBLIGATORISK PÅ DENNE VEIEN (Codex P1).
            # 057 definerer `kandidat_avmaskering` som lagret for nettopp
            # token→klartekst-kartet, og hver evaluering PRODUSERER det —
            # men veien inn bar det ikke, og den promoterte rapporten
            # stripper det med vilje. Kartet forsvant dermed når
            # arbeideren døde, og igjen sto blindet kildetekst med
            # `[NAVN-1]`-tokener ingen autorisert leser kunne løse opp.
            #
            # Feltet er KREVD, ikke valgfritt: et utelatt felt ville gitt
            # nøyaktig den stille ikke-lagringen funnet handler om. Tom
            # dict er derimot lovlig — det er formen blinding AVSKRUDD
            # (auditert handling, `blinding.evalueringsinput`) gir, og
            # 057s CHECK krever `felter IS NOT NULL`, ikke ikke-tom.
            avmaskering = kropp.get("avmaskering")
            if not isinstance(artefakt, dict) or not artefakt \
                    or not isinstance(avmaskering, dict) \
                    or not all(isinstance(t, str) and isinstance(v, str)
                               for t, v in avmaskering.items()) \
                    or (sporsmal is not None
                        and not isinstance(sporsmal, list)):
                conn.rollback()
                tjeneste.logg.hendelse("request_feilformet", rid, tenant)
                return _feilsvar("request_feilformet", rid)
            # Kanonisk JSON så payload-likheten er byte-veldefinert på
            # tvers av retries — samme dict, samme streng.
            #
            # ALLE TRE MÅLES, OG SAMLET (Codex P2). Linjen under målte
            # bare `artefakt`. `avmaskering` og `intervjusporsmal` er
            # like fullt PERSISTERTE payloads — hver sin JSONB-rad, hver
            # sin hashing og hver sin likhetssammenligning ved retry — og
            # de gikk inn uten noe dekodet tak i det hele tatt. Det
            # eneste som bandt dem var wire-taket
            # `MAKS_KANDIDATARTEFAKT_KROPP`, som per konstruksjon er ~6×
            # budsjettet (JSON-eskapefaktoren): en autentisert claimant
            # kunne dermed lagre ~301 MiB kart eller spørsmålsliste under
            # et uttalt 50 MiB-budsjett, med hashing, lagring og
            # retry-sammenligning på hele mengden.
            #
            # SUMMEN, ikke tre separate tak: budsjettet er KANDIDATENS,
            # og tre uavhengige tak à 50 MiB ville vært 150 MiB under
            # samme navn. Det er også nøyaktig forholdet wire-taket alt
            # er utledet av, så de to tallene fortsetter å bety det samme.
            raa_a = json.dumps(artefakt, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"))
            raa_m = json.dumps(avmaskering, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"))
            raa_s = json.dumps(sporsmal or [], ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"))
            # DØREN MÅLER, IKKE TRANSPORTEN (samme form som dokumentveien
            # over): kroppstaket er wire-formen med verste-falls
            # JSON-ekspansjon, mens budsjettet payloadene faktisk skal
            # holde seg innenfor er den KANONISKE størrelsen. Uten denne
            # linjen var 256 KiB-fallet det eneste som bandt en JSONB-rad
            # i det hele tatt, og å heve taket ville fjernet grensen i
            # stedet for å flytte den.
            #
            # OG NUL FELLES HER SOM PÅ DOKUMENTVEIEN (Codex P2, samme
            # klasse): `jsonb` kan ikke bære en nullbyte noe
            # mer enn `TEXT` kan. `kildetekst` er den samme uttrekksteksten
            # dokumentveien bærer, og avmaskeringens verdier er utsnitt
            # av den, så nullbyten når hit langs nøyaktig samme vei.
            #
            # PÅ VERDIENE, IKKE PÅ JSON-TEKSTEN (Codex P2, runde 2).
            # Denne porten målte `"\x00" in raa_*`, og der finnes den
            # aldri: `json.dumps` skriver nullbyten som de seks tegnene
            # `\u0000`. Predikatet var dødt fra første linje. Se
            # `_har_ulagringsbart_tegn` for hvorfor escapen ikke er noe
            # bedre å lete etter. STØRRELSEN måles fortsatt på de
            # kanoniske strengene — de ER det som INSERTes.
            #
            # TEGNPORTEN STÅR FØRST, OG DET ER IKKE KOSMETIKK (Codex P2,
            # #173). `or` evaluerer venstre side først, så med
            # størrelsessummen foran var det `r.encode("utf-8")` som møtte
            # et løsrevet surrogat — og `UnicodeEncodeError` derfra er
            # verken `psycopg.Error` eller noe denne porten fanger. Den
            # kom ut som en UKODET 500, på hvert eneste retryforsøk, i
            # stedet for det `request_feilformet` linjene her finnes for.
            # Tegnene måles derfor FØR noe forsøkes kodet.
            if any(_har_ulagringsbart_tegn(v)
                   for v in (artefakt, avmaskering, sporsmal or [])) \
                    or sum(len(r.encode("utf-8"))
                           for r in (raa_a, raa_m, raa_s)) \
                    > _KANDIDAT_ARTEFAKT_MAKS:
                conn.rollback()
                tjeneste.logg.hendelse("request_feilformet", rid, tenant)
                return _feilsvar("request_feilformet", rid)
            satt = conn.execute(
                "INSERT INTO kandidat_evalueringsartefakt (tenant,"
                " prosess_id, kandidat_id, artefakt, innhold_sha256)"
                " VALUES (%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                (tenant, prosess_id, kid_uuid, raa_a,
                 hashlib.sha256(raa_a.encode("utf-8")).hexdigest()
                 )).rowcount
            if not satt:
                likt = conn.execute(
                    "SELECT artefakt = %s::jsonb"
                    " FROM kandidat_evalueringsartefakt"
                    " WHERE tenant=%s AND prosess_id=%s AND kandidat_id=%s",
                    (raa_a, tenant, prosess_id, kid_uuid)).fetchone()
                if likt is None or not likt[0]:
                    return _konflikt("evalueringsartefakt")
            # Samme transaksjon som artefaktet: kartet og teksten det
            # løser opp er ETT skriv, ikke to som kan divergere. Samme
            # idempotens- og konfliktform som lagrene over. (`raa_m` er
            # kanonisert sammen med de to andre over — budsjettet er
            # kandidatens, og alle tre måles før noe INSERTes.)
            satt = conn.execute(
                "INSERT INTO kandidat_avmaskering (tenant, prosess_id,"
                " kandidat_id, felter, innhold_sha256)"
                " VALUES (%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                (tenant, prosess_id, kid_uuid, raa_m,
                 hashlib.sha256(raa_m.encode("utf-8")).hexdigest()
                 )).rowcount
            if not satt:
                likt = conn.execute(
                    "SELECT felter = %s::jsonb FROM kandidat_avmaskering"
                    " WHERE tenant=%s AND prosess_id=%s AND kandidat_id=%s",
                    (raa_m, tenant, prosess_id, kid_uuid)).fetchone()
                if likt is None or not likt[0]:
                    return _konflikt("avmaskering")
            # SYMMETRISK MED ARTEFAKT/AVMASKERING (Cursor P2-2). Armen sto
            # som `if sporsmal:`, og da var «ingen spørsmål» ikke en
            # LAGRET sannhet, men et hopp over lageret. Begge veier brøt
            # løftet i docstringen over:
            #
            #   null først, deretter liste  → INSERT-en lyktes, og et
            #     nytt svar sto stille under samme nøkkel;
            #   liste først, deretter null  → armen ble hoppet over, og
            #     den gamle lista ble stående uten at noen målte avviket.
            #
            # To sannheter under samme `(tenant, prosess_id,
            # kandidat_id)` er nøyaktig det `kandidatdata_konflikt`
            # finnes for, og et STILLE hopp over lageret måler ingen
            # divergens.
            #
            # MEN FRAVÆR SKAL IKKE MATERIALISERES (Codex P2). Forrige
            # runde løste «hoppet» ved å skrive `[]` ubetinget, og det
            # gjør absens til den ENDELIGE payloaden: 057-lageret er
            # append-only, triggeren tillater ingen UPDATE av payload, og
            # `(tenant, prosess_id, kandidat_id)` er primærnøkkelen. Hver
            # evaluering produserer med vilje null spørsmål (#225, eiers
            # retning 27/8: de hører til innkallingen av de beste, ikke
            # evalueringen av alle), så raden ble skrevet for HVER
            # kandidat — og la permanent beslag på nøkkelen det senere
            # innkallings-/shortlist-steget skal skrive under. Lageret
            # som er utpekt som kilden for genererte spørsmål var dermed
            # fylt med tomhet før den flyten fikk eksistere.
            #
            # Begge kravene holder samtidig ved å skille PÅSTAND fra
            # LAGRING: en liste skrives og måles som de to lagrene over,
            # mens ingen liste ikke skriver noe — men fortsatt MÅLER at
            # ingen står der fra før. Divergensen forrige runde pekte på
            # («liste først, deretter null») blir da fortsatt en
            # `kandidatdata_konflikt`, ikke et stille hopp. (`raa_s`
            # kanoniseres sammen med de to andre over, av samme grunn:
            # budsjettet er kandidatens og måles før noe INSERTes.)
            #
            # RESTRISIKO, SAGT HØYT: skriver innkallingssteget spørsmål
            # og en SEN retry av denne evalueringen kommer etterpå, ser
            # målingen en rad og svarer `kandidatdata_konflikt`. Vinduet
            # er leasens, og alternativet — å la raden stå tom for alltid
            # — stenger flyten for hver eneste kandidat.
            if sporsmal:
                satt = conn.execute(
                    "INSERT INTO kandidat_intervjusporsmal (tenant,"
                    " prosess_id, kandidat_id, sporsmal, innhold_sha256)"
                    " VALUES (%s,%s,%s,%s::jsonb,%s)"
                    " ON CONFLICT DO NOTHING",
                    (tenant, prosess_id, kid_uuid, raa_s,
                     hashlib.sha256(raa_s.encode("utf-8")).hexdigest()
                     )).rowcount
                if not satt:
                    likt = conn.execute(
                        "SELECT sporsmal = %s::jsonb"
                        " FROM kandidat_intervjusporsmal"
                        " WHERE tenant=%s AND prosess_id=%s"
                        "   AND kandidat_id=%s",
                        (raa_s, tenant, prosess_id, kid_uuid)).fetchone()
                    if likt is None or not likt[0]:
                        return _konflikt("intervjusporsmal")
            else:
                staar = conn.execute(
                    "SELECT 1 FROM kandidat_intervjusporsmal"
                    " WHERE tenant=%s AND prosess_id=%s"
                    "   AND kandidat_id=%s",
                    (tenant, prosess_id, kid_uuid)).fetchone()
                if staar is not None:
                    return _konflikt("intervjusporsmal")
        # OG LEASEN MÅLES PÅ NYTT VED SKRIVEGRENSEN (Codex P2). Leddet
        # over var den ENESTE tidsmålingen, og den står før
        # base64-dekodingen, hashingen og INSERT-ene av inntil 25–50 MiB.
        # Radlåsen serialiserer TILSTANDSENDRINGER — en claim-tyv og
        # `forny_oppdragslease` tar begge `FOR UPDATE` og venter på oss —
        # men den stopper ikke VEGGKLOKKEN. Verre: mens vi holder `FOR
        # SHARE` kan heartbeaten ikke ta sin egen lås, så leasen kan
        # ikke engang fornyes underveis. En forespørsel med lite tid
        # igjen kunne derfor passere porten over, bruke sekundene sine på
        # å skrive, og committe persondata på en fullmakt som var utløpt
        # da raden landet.
        #
        # Målingen gjentas derfor der skrivingen faktisk blir varig, i
        # SAMME transaksjon og med samme `clock_timestamp()`. Claimet
        # måles med — ikke fordi det kan ha endret seg under låsen, men
        # fordi en port som fanger utfallet skal stå på egne ben om en
        # framtidig skriver glemmer `FOR UPDATE`. Retningen er
        # fail-closed: en lease som døde i skrivevinduet ruller tilbake
        # og svarer som en avvist claim, aldri et stille ja.
        levende = conn.execute(
            "SELECT 1 FROM oppdrag"
            " WHERE tenant=%s AND id=%s AND status='plukket'"
            "   AND owner_claim_id=%s AND owner_generation=%s"
            "   AND owner_lease_utloper IS NOT NULL"
            "   AND owner_lease_utloper > clock_timestamp()",
            (tenant, opp_id, claim_id, generasjon)).fetchone()
        if levende is None:
            conn.rollback()
            tjeneste.logg.hendelse("kandidatdata_avvist", rid, tenant,
                                   art="sikkerhet", oppdrag_id=opp_id,
                                   detalj="lease_utlopt_under_skriving")
            return _feilsvar("kandidatdata_avvist", rid)
        conn.commit()
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    except psycopg.Error as e:
        conn.rollback()
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                               feiltype=type(e).__name__)
        return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


def _oppdrag_forny(tjeneste: Tjeneste, request: Request) -> Response:
    """Fornyelsesveien (063/#165): heartbeat fra den SITTENDE utføreren.

    Autentiseringen er kvitteringens form: et modultoken svarer på hvilken
    deployment dette er, og fullmakten er CLAIMETS — kroppen må bære
    nøyaktig (oppdrag_id, owner_claim_id, owner_generation), og døren
    matcher raden radlåst. En død lease kan aldri fornyes (fencing), og
    en rullet modulepoch feller fornyelsen (port 24-formen, målt i døren
    mot levende modulhode).

    Svaret bærer ny leaseutløper OG en FERSK opplastingskapabilitet
    (samme serverkontekst-utledning som claim — den gamle kapabiliteten
    var klemt til sitt eget grant-vindu og kan være død): en utfører som
    lever forbi første time mister ellers leveringsretten midt i lovlig
    arbeid. Kvitteringskapabiliteten lever til evidensfristen og trenger
    aldri fornyelse.
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
        # Samme unntak som kvitteringen (035): modultokenet bærer ingen
        # scopes — fullmakten er claimets, og bindingen under er smalere
        # enn noe scope kunne vært.
        if not isinstance(auth, ModulAutentisert) and not _modulscope(auth):
            tjeneste.logg.hendelse("scope_mangler", rid, auth.tenant,
                                   scope=ORDRESCOPE + "<prefiks>")
            return _feilsvar("scope_mangler", rid)
        # Samme ratebudsjett som claim/upload (CodeRabbit): et heartbeat
        # i løkke er billig for kalleren og skal ikke være gratis her.
        if not tjeneste.rate.slipp_gjennom(auth.token_id):
            tjeneste.logg.hendelse("rate_grense", rid, auth.tenant)
            return _feilsvar("rate_grense", rid)
        # Revalideringen (CodeRabbit, kritisk): `noddeaktiver_modul`
        # terminerer tokenfamilien ØYEBLIKKELIG, og fornyelsen er
        # nøyaktig veien et drept token ville brukt til å holde liv i
        # claimet sitt — samme port som claim/kvittering/upload.
        if isinstance(auth, ModulAutentisert):
            revalidering = _modultoken_revalidert(tjeneste, conn, auth, rid)
            if revalidering is not None:
                return revalidering

        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            kropp = json.loads(raa.decode("utf-8"))
        except Exception:
            kropp = None
        opp_id = kropp.get("oppdrag_id") if isinstance(kropp, dict) else None
        claim_id = kropp.get("owner_claim_id") \
            if isinstance(kropp, dict) else None
        generasjon = kropp.get("owner_generation") \
            if isinstance(kropp, dict) else None
        lease_s = kropp.get("lease_s", 300) if isinstance(kropp, dict) else 300
        if not isinstance(opp_id, int) or isinstance(opp_id, bool) \
                or not isinstance(claim_id, str) or not claim_id \
                or not isinstance(generasjon, int) \
                or isinstance(generasjon, bool) \
                or not isinstance(lease_s, int) or isinstance(lease_s, bool):
            tjeneste.logg.hendelse("request_feilformet", rid, auth.tenant)
            return _feilsvar("request_feilformet", rid)

        modul = auth.modul_id if isinstance(auth, ModulAutentisert) \
            else auth.rolle
        try:
            rad = conn.execute(
                "SELECT owner_lease_utloper, tenant, modul_id,"
                " kontraktversjon, kontrakt_hash, module_epoch, evidensfrist"
                " FROM forny_oppdragslease(%s,%s,%s,%s,%s)",
                (opp_id, modul, claim_id, generasjon, lease_s)).fetchone()
        except psycopg.errors.NoDataFound:
            conn.rollback()
            tjeneste.logg.hendelse("lease_ikke_fornybar", rid, auth.tenant,
                                   art="sikkerhet", oppdrag_id=opp_id)
            return _feilsvar("lease_ikke_fornybar", rid)
        except psycopg.errors.ObjectNotInPrerequisiteState:
            conn.rollback()
            tjeneste.logg.hendelse("lease_utlopt", rid, auth.tenant, art="drift",
                                   oppdrag_id=opp_id)
            return _feilsvar("lease_utlopt", rid)
        except psycopg.errors.InvalidAuthorizationSpecification:
            conn.rollback()
            tjeneste.logg.hendelse("modulepoch_utdatert", rid, auth.tenant,
                                   art="sikkerhet", oppdrag_id=opp_id)
            return _feilsvar("modulepoch_utdatert", rid)
        except psycopg.errors.InvalidParameterValue:
            conn.rollback()
            tjeneste.logg.hendelse("request_feilformet", rid, auth.tenant)
            return _feilsvar("request_feilformet", rid)

        # Fersk opplastingskapabilitet i SAMME transaksjon som
        # fornyelsen: radlåsen fra døren holder til commit, så epoken
        # kapabiliteten stemples med er den fornyelsen målte.
        # RLS-konteksten settes til OPPDRAGETS tenant (dørens svar, aldri
        # kallerens påstand) — utledningen leser `oppdrag` som runtime.
        sett_kontekst(conn, rad[1], auth.aktor, rid)
        opplasting = _utled_opplastingskapabilitet(
            conn, auth, rad[1], opp_id, rad[6])
        conn.commit()
        tjeneste.logg.hendelse("lease_fornyet", rid, rad[1], art="drift",
                               oppdrag_id=opp_id)
        return kanonisk_json({
            "oppdrag_id": opp_id,
            "owner_lease_utloper": rad[0].isoformat(),
            "opplasting": opplasting,
            "request_id": rid}, 200, {"x-request-id": rid})
    except psycopg.Error as e:
        conn.rollback()
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                               feiltype=type(e).__name__)
        return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


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
        # 035: et modultoken bærer INGEN scopes — det svarer på ett spørsmål
        # (hvilken deployment er dette?), og fullmakten til å kvittere er
        # ikke tokenets, men OPPDRAGETS: `kvittering_jti` ble utstedt av
        # claim-en, er bundet til nøyaktig ett oppdrag OG til eiermodulen,
        # og innløses mot `auth.rolle` noen linjer ned. Den bindingen er
        # smalere enn `orders:execute:<prefiks>` kunne vært, så
        # legacy-porten er ikke bare uoppfylt her — den er overflødig.
        # Uten dette unntaket kunne en onboardet deployment claime arbeid
        # den aldri fikk levere resultatet av.
        if not isinstance(auth, ModulAutentisert) and not _modulscope(auth):
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
    if unntak_id is None:
        # 038 §5: beslutningsoppdrag — avvisningen ble ført på saken
        # `sikre_sak_for_oppdrag` fant/fødte, og DEN id-en har ikke denne
        # veien. Detaljene bærer oppdrag+artefakt og er nøkkelen som
        # faktisk identifiserer kvitteringen; et `unntak_id = NULL`-filter
        # hadde stille gjort enhver gjentatt avvist kvittering til 200.
        return conn.execute(
            "SELECT 1 FROM unntak_historikk WHERE tenant=%s"
            " AND hendelse='artefakt_ikke_verifisert'"
            " AND detalj->>'oppdrag_id' = %s AND detalj->>'artefakt_id' = %s",
            (tenant, str(oppdrag_id),
             str(artefakt_id))).fetchone() is not None
    return conn.execute(
        "SELECT 1 FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        " AND hendelse='artefakt_ikke_verifisert'"
        " AND detalj->>'oppdrag_id' = %s AND detalj->>'artefakt_id' = %s",
        (tenant, unntak_id, str(oppdrag_id),
         str(artefakt_id))).fetchone() is not None


def _idempotent_svar(conn, *, tenant: str, oppdrag_id: int, ny_hash: str,
                     rid: str) -> Response:
    """Svaret på en gjentakelse: sa den forrige kvitteringen NOE OM STATUS?

    `status: "idempotent"` betydde begge deler på én gang (Codex P2), og
    det er den samme sammenblandingen `lagret_uten_statusendring` ble
    innført for å fjerne. Den FØRSTE kvitteringen tar én av to veier:

      * den avsluttende: `oppdrag.status` settes til `utfort`/`feilet` og
        `oppdrag.resultathash` til hashen — oppdraget ER ferdig;
      * sen evidens: bare kapabiliteten brennes med hashen. Status og sak
        røres ikke med vilje — «en sen kvittering er evidens, og skal
        aldri avslutte noe» — og svaret sier det selv, med 202.

    Begge etterlater `kapabilitet.resultathash = ny_hash`, så en
    gjentakelse traff samme idempotensgren uansett hvilken vei den første
    tok, og fikk det samme ordet for to helt ulike tilstander. Utfører
    utføreren en retry — helt lovlig, kvitteringen ER idempotent — måtte
    den enten tro at et ufullført oppdrag var ferdig, eller (som
    `wcag_audit.controller` valgte, fail-closed) at et ferdig oppdrag var
    ukvittert. Ingen av dem er sanne, og ingen av dem kan utledes av
    svaret.

    Autoriteten er oppdragsraden, ikke kapabiliteten: statusen må være
    terminal OG `resultathash` må være VÅR hash. Har et ANNET resultat
    avsluttet oppdraget, er dette ikke et idempotent gjensyn med vår egen
    kvittering, og da svarer vi det konservative — samme retning som
    resten av porten.

    Leses FØR kallerens rollback, som `_kvittering_alt_avvist`: READ
    COMMITTED gir setningen et ferskt snapshot, så kappløpsvinnerens
    committede tilstand er synlig.
    """
    rad = conn.execute(
        "SELECT status, resultathash FROM oppdrag WHERE tenant=%s AND id=%s",
        (tenant, oppdrag_id)).fetchone()
    skiftet = (rad is not None and rad[0] in ("utfort", "feilet")
               and rad[1] == ny_hash)
    return kanonisk_json(
        {"status": "idempotent" if skiftet
                   else "idempotent_uten_statusendring",
         "oppdrag_id": oppdrag_id, "request_id": rid}, 200,
        {"x-request-id": rid})


def _forbruk_kapabilitet(tjeneste: Tjeneste, conn, jti: str, ny_hash: str, *,
                         tenant: str, unntak_id: int, oppdrag_id: int,
                         rid: str, artefakt_id=None,
                         sen: bool = False) -> Response | None:
    """Forbruker kapabiliteten, eller klassifiserer hvorfor vi ikke kunne.

    -> None betyr «kapabiliteten er VÅR, fortsett». Alt annet er et ferdig
    svar, og transaksjonen er avsluttet.

    `sen=True` er sen-evidensveien (043, Codex P1). Der kan kapabiliteten
    være brent `avvist` av et menneskelig nei, og toargsformen svarer
    `ugyldig` på den — for evig, siden retryen bærer samme jti. Da rullet
    denne funksjonen tilbake med `kapabilitet_ugyldig` FØR sen-evidens-
    grenen ble nådd, og en gyldig sen kvittering kunne aldri skrive
    `sen_kvittering` eller føde kompensasjons-/irreversibilitetssaken §5
    lover. Treargsformens `sen_evidens` fester hashen på den avviste
    kapabiliteten uten å røre statusen: `avvist` er fortsatt terminal,
    oppdraget fortsatt kansellert — men evidensen kommer inn, og
    idempotens/konflikt gjelder også her.

    Den AVSLUTTENDE veien bruker bevisst ikke `sen_evidens`: taper den
    kappløpet mot et nei, skal den fortsatt fail-close (`kapabilitet_
    ugyldig`) og ikke fortsette til statusskiftet.

    Delt av BEGGE veiene — den avsluttende og sen-evidensveien. Det er ikke
    en stilsak: forrige runde viste hva som skjer når en regel bare er
    implementert i den ene av to grener, og runden før det viste det samme.
    Med én funksjon kan de to veiene ikke lenger gli fra hverandre.

    Klassifiseringen selv gjøres ATOMISK i databasen (se
    `bruk_kvitteringskapabilitet`). Å lese tilstanden herfra etterpå ville
    vært et nytt kappløp for å avgjøre utfallet av det første.
    """
    if sen:
        utfall = conn.execute(
            "SELECT bruk_kvitteringskapabilitet(%s,%s,'sen_evidens')",
            (jti, ny_hash)).fetchone()[0]
    else:
        utfall = conn.execute("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                              (jti, ny_hash)).fetchone()[0]
    if utfall in ("brukt", "sen_evidens"):
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
        # Samme lesning som den sekvensielle retryen, samme funksjon: hvilken
        # vei vinneren tok avgjør hva taperen får vite (Codex P2).
        svar = _idempotent_svar(conn, tenant=tenant, oppdrag_id=oppdrag_id,
                                ny_hash=ny_hash, rid=rid)
        conn.rollback()
        if avvist:
            tjeneste.logg.hendelse("kvittering_konflikt", rid, tenant,
                                   oppdrag_id=oppdrag_id, kapplop=True)
            return _feilsvar("kvittering_konflikt", rid)
        return svar

    if utfall == "konflikt":
        # To ULIKE resultater levert samtidig. Uten denne grenen forsvant
        # forsøket på motstridende evidens som et generisk auth-avvik —
        # altså nøyaktig den hendelsen sikkerhetssaken finnes for.
        _sikkerhetssak_kvittering(conn, tenant, unntak_id,
                                  "motstridende_kvittering",
                                  {"kilde": "kapplop", "ny": ny_hash,
                                   "oppdrag_id": oppdrag_id}, rid,
                                  oppdrag_id=oppdrag_id)
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

    Med ÉN presis unntagelse (043 §5, Codex P2 runde 8): et oppdrag et
    menneske har kansellert er TERMINALT. Det kan aldri claimes igjen, så
    det finnes ingen ny eier å blokkere — og der er uforanderligheten
    nettopp det evidensen skal ha. Den sene kvitteringen som utløser
    kompensasjons-/irreversibilitetssaken lagres derfor signert på raden,
    så saken kan legge fram grunnlaget sitt og ikke bare påstå det.
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
    #
    #    Codex P1: `auth.rolle` er MODULENS id, og den er delt mellom alle
    #    levende deployments av modulen — staging og produksjon, eller to
    #    releaser under hver sin kontraktversjon, hver med sitt eget
    #    modultoken. Kvitteringsveien slipper dem alle forbi scope-porten
    #    med vilje (retten ER kapabilitetens), så modulnavnet alene var
    #    ingen port: en delt eller feilrutet `kvittering_jti` lot en annen
    #    deployment enn den som claimet levere resultatet og avslutte
    #    jobben. Innløsningen krever derfor HELE den autentiserte
    #    deploymenten. Med et legacy-api-token finnes ingen — NULL matcher
    #    da kun kapabiliteter som selv er deploymentløse (fail-closed begge
    #    veier), som på opplastingsveien.
    #
    #    Codex P1, neste runde: identiteten er ikke NOK. `preauth` lukket
    #    sin egen transaksjon, og et nødstopp som committet etterpå har
    #    drept tokenet uten at dette `auth`-objektet vet det. Derfor
    #    revalideres deploymenten FØR innløsningen, under modul-låsen —
    #    ellers kunne den stoppede deploymenten fortsatt avslutte jobben.
    revalidering = _modultoken_revalidert(tjeneste, conn, auth, rid)
    if revalidering is not None:
        return revalidering
    d_miljo = getattr(auth, "miljo", None)
    d_release = getattr(auth, "release_id", None)
    kap = conn.execute(
        "SELECT tenant, oppdrag_id, owner_claim_id, owner_generation, status,"
        " resultathash FROM innlos_kvitteringskapabilitet(%s, %s, %s, %s)",
        (jti, auth.rolle, d_miljo, d_release)).fetchone()
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

    # Codex P1: en SUKSESS for en artefaktproduserende type må BÆRE
    # artefaktet. `er_utforelseskvittering` krever ingen av artefaktfeltene,
    # og hele artefaktgrenen nedenfor står under `if art_id is not None` —
    # en vellykket kvittering uten `artefakt_id` hoppet derfor over
    # promotering, bindingskontroll, epoch-sjekk OG skjemarevalideringen og
    # falt rett ned i statusskiftet: `oppdrag.status = utfort`, `unntak =
    # løst`, uten en eneste rapport å vise til. En WCAG-kontroll uten
    # evidens er ikke en utført kontroll, og her ville ingen engang sett at
    # den manglet.
    #
    # Kravet står på TYPEN (`produserer_artefakt`), ikke som en fast liste
    # her: legacy-typer uten artefakt er helt urørt, og en FEILET kvittering
    # har per definisjon ingen rapport og skal fortsatt kunne meldes uten.
    # Sjekken ligger sammen med de andre strukturvaktene, altså FØR
    # kapabiliteten forbrukes — en kvittering vi avviser skal ikke brenne
    # den controllerens ene sjanse til å levere den samme rapporten på nytt.
    if oppdragskontrakt.mangler_artefaktevidens(oppdragstype, kvittering):
        conn.rollback()
        tjeneste.logg.hendelse("request_feilformet", rid, tenant,
                               oppdrag_id=oppdrag_id,
                               grunn="artefakt_paakrevd",
                               oppdragstype=oppdragstype)
        return _feilsvar("request_feilformet", rid)

    # ... og den ANDRE halvdelen av den samme setningen (Codex P2): en
    # FEILET kvittering har per definisjon ingen rapport. Bare den ene
    # halvdelen var håndhevet. Artefaktgrenen nedenfor står under `if
    # art_id is not None` og ikke under resultatet, så en autentisert
    # modul som sendte `resultat: "feilet"` sammen med en gyldig
    # `artefakt_id` og hash fikk rapporten PROMOTERT til attestert
    # evidens — og deretter ble oppdraget merket feilet. Det er en
    # selvmotsigende tilstand å lagre: en konsument som leser rapporten
    # ser en fullført kontroll, en som leser oppdraget ser en mislykket.
    #
    # Står sammen med vakten over, altså før kapabiliteten forbrukes: en
    # kvittering vi avviser skal ikke brenne utførerens ene sjanse til å
    # sende den riktige.
    if oppdragskontrakt.artefakt_uten_utforelse(kvittering):
        conn.rollback()
        tjeneste.logg.hendelse("request_feilformet", rid, tenant,
                               oppdrag_id=oppdrag_id,
                               grunn="artefakt_uten_utforelse",
                               oppdragstype=oppdragstype)
        return _feilsvar("request_feilformet", rid)

    ny_hash = _resultathash(kvittering)

    # 3c. LÅSEORDEN: SAKEN FØRST (043, Codex P1 runde 3).
    #
    # Avvis-veien tar tre rader, i denne rekkefølgen:
    #   sak (`unntak`)  →  kvitteringskapabilitet  →  oppdrag
    # `behandle_unntakshandling` låser saken med `FOR UPDATE`
    # (`unntaksbehandling.py`, steg 2) og HOLDER den gjennom hele
    # operatørhandlingen; inne i den låsen pre-låser `avvis_med_opplosning`
    # kapabilitetene og deretter oppdragene (043 §7).
    #
    # Kvitteringsveien tok de SAMME tre radene i motsatt ende: den brant
    # kapabiliteten i `_forbruk_kapabilitet` og rørte saken først til slutt
    # (historikkraden + `UPDATE unntak`). Da kan avvis-veien holde saken og
    # vente på kapabiliteten mens kvitteringsveien holder kapabiliteten og
    # venter på saken — PostgreSQL avbryter én med 40P01. Kappløpet skal
    # avgjøres av hvem som brenner kapabiliteten først, og ende i et
    # AVGJORT utfall (`oppdrag_utfort` eller gjennomført kansellering);
    # en vranglåsfeil er ingen av delene. Pre-passet i 043 §7 rettet bare
    # den indre halvparten (kapabilitet før oppdrag) — den ytre sakslåsen
    # sto igjen, og det er nettopp den Codex fant.
    #
    # Saken låses derfor HER, før første kapabilitets- eller oppdragslås, og
    # holdes til commit: begge veier tar radene i samme rekkefølge, og den
    # ene venter på den andre i stedet for at begge dør. Punktet er valgt
    # etter alle struktur-/signaturvaktene — en kvittering vi uansett
    # avviser skal ikke stå i kø bak en operatørhandling.
    #
    # ... OG SAKEN HAR TO RELASJONSRETNINGER (Codex P1, runde 5).
    #
    # `o.unntak_id` er OPPHAV, ikke generell sakstilknytning (038): et
    # BESLUTNINGSoppdrag har den NULL, og saken peker den andre veien
    # (`unntak.oppdrag_id`). Denne låsen sto bak `unntak_id is not None` og
    # så derfor bare reparasjonsopphavet — mens §4 i 043 gjorde nettopp den
    # andre koblingen avvisbar: `sak_utestaaende` finner beslutningsoppdrag
    # GJENNOM `unntak.oppdrag_id`, og `avvis_med_opplosning` godtar dem som
    # oppløsningsmål (§7). For akkurat de oppdragene hoppet kvitteringsveien
    # over både låsen og oppfriskningen under den, og tapet fra runde 4 var
    # tilbake i sin helhet: nei-et rekker å committe kansellering og
    # `avvist`, kvitteringen regner videre på `plukket`/`utstedt`, `bruk_
    # kvitteringskapabilitet` (toargs) svarer `ugyldig`, og den signerte
    # sene evidensen — med kompensasjons-/irreversibilitetssaken §5 skal
    # føde — går tapt i stillhet.
    #
    # Saken finnes derfor gjennom BEGGE retningene, i ÉN setning og i
    # stigende id: mengden er den samme autoriteten §7 krever av
    # oppløsningsmålene, og en deterministisk rekkefølge holder to
    # kvitteringsveier fra å ta flere saker i motsatt orden. `unntak_id`
    # selv røres ikke — den betyr fortsatt OPPHAV nedenfor.
    #
    # Er det ingen sak i noen av retningene, er det ingen felles rad å
    # ordne: avvis-veien kan ikke nå oppdraget (den finner oppdrag gjennom
    # saken), og en sak som fødes lenger nede av `sikre_sak_for_oppdrag` er
    # per definisjon ny — ingen annen transaksjon holder den.
    laaste_saker = conn.execute(
        "SELECT u.id FROM unntak u"
        " WHERE u.tenant=%s AND (u.id=%s OR u.oppdrag_id=%s)"
        " ORDER BY u.id FOR UPDATE",
        (tenant, unntak_id, oppdrag_id)).fetchall()
    if laaste_saker:

        # ... OG DA MÅ TILSTANDEN LESES PÅ NYTT (Codex P1, runde 4).
        #
        # En lås som bare ordner rekkefølgen, uten at det som leses etterpå
        # er lest UNDER den, gjør bare vranglåsen om til en stille feil.
        # Kapabiliteten (steg 1) og oppdragsraden (steg 1b) ble lest FØR
        # låsen. Kommer inntaket hit mens et menneskelig nei holder saken,
        # venter vi her til det har committet — og fortsetter så å regne på
        # verdier fra tiden før nei-et: `kap_status` er fortsatt `utstedt`,
        # `status` fortsatt `plukket`, generasjonen fortsatt eierens.
        #
        # Utfallet var det motsatte av det 043 §5 er til for: `kan_avslutte`
        # ble True, den ORDINÆRE toargsbrenningen kjørte mot en kapabilitet
        # som nå står `avvist`, og `_forbruk_kapabilitet` rullet tilbake med
        # `kapabilitet_ugyldig`. Den signerte kvitteringen ble aldri skrevet
        # som `sen_kvittering`, og kompensasjons-/irreversibilitetssaken ble
        # aldri født — nøyaktig det tapet sen-evidensveien ble bygget for å
        # hindre, gjenåpnet av låsen som skulle beskytte den.
        #
        # Låsen er det eneste punktet som gir et STABILT bilde: fra her og
        # ut kan ingen avvis-vei endre disse radene før vi committer.
        # Leses de her, ser vi nei-et og velger sen-evidensveien; forsvant
        # kapabiliteten helt (terminal `feilet`/utløpt) eller oppdraget,
        # svarer vi som førstelesningen gjorde — fail-closed.
        #
        # `naa` er BEVISST ikke oppfrisket: den er kvitteringens ANKOMSTTID,
        # målt før enhver låskø. Et oppdrag skal ikke miste fristen sin
        # fordi vår transaksjon sto bak en operatørhandling.
        #
        # ... og DET SAMME GJELDER KAPABILITETENS UTLØP (Codex P2, runde 5).
        # Innvendingen var at `innlos_kvitteringskapabilitet` filtrerer på
        # `k.utloper > now()`, så en kvittering som ankom i tide, men sto i
        # sakslåskø forbi utløpet, skulle miste kapabiliteten her. Den
        # egenskapen HAR koden allerede, og den er ikke en tilfeldighet:
        # `now()` er transaksjonstidsstempelet (`transaction_timestamp()`),
        # ikke veggklokka, og hele ingesten kjører i ÉN transaksjon —
        # `preauth` lukker sin egen, og forretningstransaksjonen begynner
        # ved den første lesningen over. Låskøen kan derfor ikke flytte
        # utløpsgrensen: begge innløsningene måler mot nøyaktig samme
        # tidspunkt, og det ligger før ankomsten (`naa`). Ville vi i stedet
        # ha friskepunktets veggklokke, måtte vi bedt om
        # `statement_timestamp()` — og det er nettopp det vi IKKE gjør.
        # Egenskapen er målt, ikke bare beskrevet: se
        # `test_P1_sakslaskoen_tar_ikke_kapabilitetens_frist` (test_m37).
        kap = conn.execute(
            "SELECT owner_claim_id, owner_generation, status, resultathash"
            "  FROM innlos_kvitteringskapabilitet(%s, %s, %s, %s)",
            (jti, auth.rolle, d_miljo, d_release)).fetchone()
        if kap is None:
            conn.rollback()
            tjeneste.logg.hendelse("kapabilitet_ugyldig", rid, tenant,
                                   oppdrag_id=oppdrag_id, grunn="etter_sakslas")
            return _feilsvar("kapabilitet_ugyldig", rid)
        kap_claim, kap_gen, kap_status, kap_hash = kap

        rad = conn.execute(
            "SELECT o.status, o.owner_claim_id, o.owner_generation,"
            " o.utforelsesfrist, o.resultathash"
            "  FROM oppdrag o WHERE o.tenant=%s AND o.id=%s",
            (tenant, oppdrag_id)).fetchone()
        if rad is None:
            conn.rollback()
            return _feilsvar("kapabilitet_ugyldig", rid)
        status, owner_claim, owner_gen, uf, lagret_hash = rad

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
            # ... og var den IKKE avvist: sa den forrige kvitteringen noe om
            # status, eller ble den bare bevart som sen evidens? Se
            # `_idempotent_svar` (Codex P2).
            svar = _idempotent_svar(conn, tenant=tenant, oppdrag_id=oppdrag_id,
                                    ny_hash=ny_hash, rid=rid)
            conn.rollback()
            return svar
        _sikkerhetssak_kvittering(conn, tenant, unntak_id,
                                  "motstridende_kvittering",
                                  {"kilde": kilde, "lagret": hash_,
                                   "ny": ny_hash, "oppdrag_id": oppdrag_id},
                                  rid, oppdrag_id=oppdrag_id)
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
        #
        # `sen=True`: en kapabilitet brent `avvist` av et menneskelig nei
        # skal slippe evidensen inn her (043, Codex P1) — ikke svare
        # `kapabilitet_ugyldig` og dermed gjøre §5-saken uoppnåelig.
        svar = _forbruk_kapabilitet(tjeneste, conn, jti, ny_hash,
                                    tenant=tenant, unntak_id=unntak_id,
                                    oppdrag_id=oppdrag_id, rid=rid,
                                    artefakt_id=kvittering.get("artefakt_id"),
                                    sen=True)
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
        #
        # REVERSIBILITETEN AVGJØRES FØR BEVARINGEN (043, Codex P2 runde 2).
        # `bevar_artefakt` setter artefaktet `bevart` = RETAINED og terminalt,
        # og oppryddingen rører det aldri igjen. For et `direkte` oppdrag som
        # mennesket kansellerte sier kontrakten — og avsnittet under — at
        # resultatet FORKASTES og artefaktet skal ryddes; bevares det først,
        # blir «ryddes» til «beholdes for alltid». Oppslaget er derfor flyttet
        # hit opp: det avgjør OM artefaktet skal bevares, og gjenbrukes så av
        # §5-saken lenger nede.
        kans = conn.execute(
            "SELECT kansellert_aarsak FROM oppdrag WHERE tenant=%s AND id=%s",
            (tenant, oppdrag_id)).fetchone()
        menneskelig_nei = kans is not None and kans[0] == "menneskelig_avvis"
        reversibilitet = conn.execute(
            "SELECT reversibilitet_for_oppdrag(%s,%s)",
            (tenant, oppdrag_id)).fetchone()[0] if menneskelig_nei else None
        bevar = not (menneskelig_nei and reversibilitet == "direkte")
        sen_artefakt = kvittering.get("artefakt_id")
        if sen_artefakt is not None:
            # bevar_artefakt validerer (tenant/oppdrag/signert hash), låser raden
            # (serialiserer mot oppryddingen) og bevarer den atomisk. 'ugyldig' =
            # fremmed/ikke-eksisterende/feil-hash/alt-nullet → sikkerhetskonflikt,
            # ikke falsk aksept. Runtime kan ikke låse artefakt selv (kun SELECT).
            # Skal artefaktet IKKE bevares, gjøres NØYAKTIG samme validering og
            # låsing av `verifiser_artefaktbinding` — bare uten skrivingen: en
            # kvittering som navngir feil artefakt skal falle like hardt på
            # `direkte`-veien, det er artefaktet som skal ryddes, ikke porten.
            utfall = conn.execute(
                "SELECT bevar_artefakt(%s,%s,%s,%s)" if bevar else
                "SELECT verifiser_artefaktbinding(%s,%s,%s,%s)",
                (sen_artefakt, tenant, oppdrag_id,
                 kvittering.get("klartekst_sha256"))).fetchone()[0]
            if utfall == "ugyldig":
                # Brenningen fra _forbruk står ved lag; klassifiser som
                # sikkerhetskonflikt (som promoteringsfeil) og commit den.
                _sikkerhetssak_kvittering(
                    conn, tenant, unntak_id, "artefakt_ikke_verifisert",
                    {"oppdrag_id": oppdrag_id, "artefakt_id": str(sen_artefakt)},
                    rid, oppdrag_id=oppdrag_id)
                conn.commit()
                tjeneste.logg.hendelse("artefakt_promotering_avvist", rid, tenant,
                                       oppdrag_id=oppdrag_id, art="sikkerhet")
                return _feilsvar("kvittering_konflikt", rid)
        if unntak_id is None and menneskelig_nei:
            # SAKEN SOM SA NEI EIER DEN SENE EVIDENSEN (Codex P1, runde 7).
            #
            # For et BESLUTNINGSoppdrag er `unntak_id` NULL med vilje —
            # saken peker tilbake gjennom `unntak.oppdrag_id`, og det er
            # nettopp den koblingen §4 gjorde avvisbar. Falt vi rett ned i
            # `sikre_sak_for_oppdrag(... 'evidensfrist' ...)` under, fikk vi
            # ikke saken tilbake: mennesket har akkurat satt den `avvist`,
            # altså TERMINAL, og gjenbruksveien (038) tar aldri en terminal
            # sak. Resultatet var en helt ny, ÅPEN evidensfrist-sak — en
            # påstand om at fristen løp ut, for en kvittering som kom i
            # TIDE — og for `kompenserende`/`irreversibel` deretter enda en
            # sak ved siden av. For `direkte`, der kontrakten sier at ingen
            # oppfølging trengs, ble den falske saken den eneste.
            #
            # Den sene evidensen hører til saken mennesket faktisk avgjorde.
            # Den er entydig identifiserbar: §7 fører `oppdrag_kansellert`
            # med oppdragets id på nøyaktig den saken nei-et ble gitt på.
            # Finner vi den ikke (en kansellering fra en vei uten spor),
            # faller vi tilbake på selve tilbakekoblingen, og først om
            # INGEN sak finnes gjelder 038 §5-veien under.
            sak_nei = conn.execute(
                "SELECT h.unntak_id FROM unntak_historikk h"
                " WHERE h.tenant=%s AND h.hendelse='oppdrag_kansellert'"
                "   AND (h.detalj->>'oppdrag_id')::bigint = %s"
                " ORDER BY h.id DESC LIMIT 1",
                (tenant, oppdrag_id)).fetchone()
            if sak_nei is None:
                sak_nei = conn.execute(
                    "SELECT u.id FROM unntak u"
                    " WHERE u.tenant=%s AND u.oppdrag_id=%s"
                    " ORDER BY u.id LIMIT 1",
                    (tenant, oppdrag_id)).fetchone()
            if sak_nei is not None:
                unntak_id = sak_nei[0]
        if unntak_id is None:
            # 038 §5: sen evidens på et beslutningsoppdrag hører til
            # evidensfrist-familien — samme sak reaperen fant/fødte da
            # fristen løp ut, idempotent også om kvitteringen kommer først.
            unntak_id = conn.execute(
                "SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',%s,%s)",
                (tenant, oppdrag_id, auth.aktor, rid)).fetchone()[0]
        # DEN SIGNERTE KVITTERINGEN SKAL OVERLEVE (Codex P2, runde 8).
        #
        # Under fødte §5-saken en påstand om at handlingen SKJEDDE — og
        # kastet så beviset: evidensraden bærer bare en oppsummering
        # (resultat + hash), kapabiliteten bare hashen, og selve den
        # signerte kvitteringen fantes ingen steder etter dette kallet. Da
        # ber saken et menneske kompensere for, eller granske, noe systemet
        # ikke lenger kan legge fram grunnlaget for. En remedieringssak uten
        # sin egen evidens er en påstand, ikke et spor.
        #
        # Raden HAR plassen for det (`kvittering`, `kvittering_signatur`,
        # `resultathash`), og grunnen til at den sene veien lot den stå tom
        # gjelder ikke her: den er at en utdatert kvittering ellers ville
        # låst raden (kolonnelåsen: uforanderlig når satt) og hindret den
        # NYE eieren i å levere sin. Det forutsetter at det KAN komme en ny
        # eier. Etter et menneskelig nei er oppdraget terminalt
        # `kansellert` — ingen kan claime det, og ingen kvittering kan
        # avslutte det noen gang. Da blokkerer lagringen ingenting, og
        # uforanderligheten er nettopp det evidensen skal ha.
        #
        # Derfor: bare på nei-grenen, og bare i det tomme feltet. Er det alt
        # fylt (en tidligere sen kvittering på samme kansellerte oppdrag),
        # rører vi det ikke — den første er den lagrede, og denne står
        # fortsatt i historikken med sin egen hash. Statusen endres ikke;
        # dette er lagring av evidens, ikke en fullføring.
        kvittering_lagret = False
        if menneskelig_nei:
            kvittering_lagret = conn.execute(
                "UPDATE oppdrag SET kvittering=%s, kvittering_signatur=%s,"
                " resultathash=%s WHERE tenant=%s AND id=%s"
                "   AND kvittering IS NULL RETURNING id",
                (json.dumps(kvittering, ensure_ascii=False),
                 (kvittering.get("signatur") or {}).get("verdi"), ny_hash,
                 tenant, oppdrag_id)).fetchone() is not None
        conn.execute(
            "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
            " request_id, detalj) VALUES (%s,%s,'sen_kvittering',%s,%s,%s)",
            (tenant, unntak_id, auth.aktor, rid,
             json.dumps({"oppdrag_id": oppdrag_id,
                         "gjeldende_fencing": gjeldende,
                         "etter_utforelsesfrist": naa > uf,
                         "resultat": kvittering.get("resultat"),
                         # Sier HVOR beviset ligger: True = denne
                         # kvitteringen står signert på oppdragsraden,
                         # False = en tidligere sen kvittering har plassen
                         # (eller oppdraget ble ikke kansellert av et
                         # menneske, og da fødes ingen §5-sak heller).
                         "kvittering_lagret": kvittering_lagret,
                         "resultathash": ny_hash}, ensure_ascii=False)))
        # 043 (Gate 14b §5): fencingen hindrer FULLFØRING, ikke det som
        # allerede skjedde. En gyldig sen kvittering på et oppdrag mennesket
        # kansellerte betyr at modulen UTFØRTE — når, i forhold til
        # operatørens klikk, vet vi ikke og påstår vi ikke (Codex P2, runde
        # 8): det eneste målte er at kvitteringen ANKOM etter kanselleringen.
        # Hva det krever av oss utledes av MODULKONTRAKTENS reversibilitet,
        # aldri av gjetning: `direkte` → ingenting (resultatet forkastes,
        # artefaktet forblir staged og ryddes av 038-reaperen);
        # `kompenserende`/`irreversibel` → sak, gjennom samme
        # `sikre_sak_for_oppdrag` som all annen sakskobling — ingen
        # parallell sakskilde, idempotent per (oppdrag, arsak), terminal
        # sak gjenbrukes aldri. Oppslaget selv er gjort FØR bevaringen (se
        # over) — nettopp fordi `direkte` også avgjør at artefaktet ikke skal
        # bevares; her brukes svaret bare til sakskoblingen.
        #
        # ... men FØRST må kvitteringen faktisk PÅSTÅ at handlingen skjedde
        # (Codex P1). Hele §5-slutningen hviler på premisset «modulen
        # utførte». En sen kvittering med
        # `resultat: "feilet"` sier det motsatte: ingen sideeffekt inntraff.
        # Den gikk likevel inn her og fødte `kompensasjon_kreves` eller
        # `irreversibel_utfort` — altså en sak som ber et menneske
        # kompensere for noe som aldri ble gjort, eller som fører i
        # revisjonssporet at en irreversibel handling er utført når
        # utføreren selv rapporterte at den ikke ble det. Evidensraden over
        # skrives fortsatt (den sene kvitteringen ER evidens, uansett
        # utfall, og bærer nå resultatet), men SLUTNINGEN krever premisset.
        #
        # ... og UKJENT REVERSIBILITET ER IKKE TRYGG (Codex P1, runde 8).
        # Mappingen var et oppslag med stille frafall: alt som ikke var
        # `kompenserende` eller `irreversibel` ga ingen sak. For `direkte`
        # er det RIKTIG — kontrakten sier at virkningen reverserer seg
        # selv. Men `reversibilitet_for_oppdrag` svarer også NULL, og det
        # betyr noe helt annet: oppdraget ble aldri modulbundet. Claim-
        # veien tillater bevisst uregistrerte oppgavetyper (037) og lar
        # modul-/kontraktbindingen stå NULL, så en slik oppgave kan utføre
        # og sende en gyldig signert `utfort`-kvittering etter nei-et —
        # og falle rett gjennom. Da er utfallet det motsatte av det §5 ble
        # bygget for: systemet har INGEN kontraktevidens for at virkningen
        # er trygg eller reverserer seg selv, og lot likevel være å
        # spørre et menneske. Fraværet av bevis er ikke bevis på fravær.
        # Mengden er derfor LUKKET med et eksplisitt fall-through: `direkte`
        # er den ene verdien som betyr «ingen oppfølging», alt annet vi ikke
        # kjenner — NULL i dag, en fremtidig klasse i morgen — blir
        # `reversibilitet_ukjent` og går til et menneske.
        sen_utfort = kvittering.get("resultat") == "utfort"
        if menneskelig_nei and sen_utfort:
            kjent = {"kompenserende": "kompensasjon_kreves",
                     "irreversibel": "irreversibel_utfort",
                     "direkte": None}
            ny_arsak = (kjent[reversibilitet] if reversibilitet in kjent
                        else "reversibilitet_ukjent")
            if ny_arsak is not None:
                conn.execute(
                    "SELECT sikre_sak_for_oppdrag(%s,%s,%s,%s,%s)",
                    (tenant, oppdrag_id, ny_arsak, auth.aktor, rid))
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
            # PR-014c §8 pkt. 2: REVALIDER MOT SAMME SKJEMA i samme
            # transaksjon som statusovergangen. Skjemaet er immutabelt, så
            # dette er ikke forsvar mot endring — det er forsvar mot at en
            # fremtidig opplastingsvei glemmer valideringen ved opplasting.
            # Brudd (eller uvaliderbart innhold) → IKKE promotert → samme
            # karantene + sikkerhetssak som binding-/epoch-avvik under:
            # artefaktet bevares for etterforskning, aldri som evidens.
            arad = conn.execute(
                "SELECT artefakttype, ciphertext, nonce, dek_ref,"
                "       skjemaversjon FROM"
                " artefakt WHERE tenant=%s AND artefakt_id=%s",
                (tenant, art_id)).fetchone()
            promotert = arad is not None
            if promotert:
                # 072: revalideringen går mot RADENS versjon — en v1
                # promotert i flippvinduet skal aldri måles mot v2s
                # skjema (og motsatt); versjonen er radens identitet.
                skjema = artefaktskjema.hent_skjema_for_versjon(
                    conn, arad[0], arad[4])
                if skjema is None:
                    promotert = False
                else:
                    try:
                        dek = kryptering.hent_dek(conn, tenant, arad[3])
                        innhold = kryptering.dekrypter(
                            dek, bytes(arad[1]), bytes(arad[2]), tenant,
                            arad[3])
                    except Exception:                         # noqa: BLE001
                        # Udekrypterbart innhold er per definisjon
                        # uvaliderbart — samme utfall, aldri en 500.
                        promotert = False
                        innhold = None
                    if innhold is not None                             and artefaktskjema.valider(skjema, innhold):
                        promotert = False
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
                {"oppdrag_id": oppdrag_id, "artefakt_id": str(art_id)}, rid,
                oppdrag_id=oppdrag_id)
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
    # RETENSJONSANKERET LUKKES VED DET FAKTISKE STATUSSKIFTET (Codex P2
    # ×3, #220). 057: kundens frist løper fra AVSLUTNINGEN — uten
    # lukkingen falt evalueringen til reaperens forlatt-frist målt fra
    # `opprettet`. Stedet er linjen OVER, ikke kapabilitetsbruken:
    # `brukt` kan ende i avvist promotering (skjema/epoch/binding), som
    # committer avvisningen med jobben fortsatt `plukket` og gjenlosbar
    # — en lukking der hadde startet fristen på et løp som ikke er
    # ferdig, og døren nekter å flytte en satt lukking når den EKTE
    # kvitteringen kommer. `sen_evidens`-veien når aldri hit. Oppslaget
    # er type-agnostisk: bare M-57-oppdrag HAR et anker.
    rad_p = conn.execute(
        "SELECT prosess_id FROM rekrutteringsprosess"
        " WHERE tenant=%s AND oppdrag_id=%s AND lukket_ts IS NULL",
        (tenant, oppdrag_id)).fetchone()
    if rad_p is not None:
        conn.execute("SELECT lukk_rekrutteringsprosess(%s,%s, now())",
                     (tenant, rad_p[0]))
    # 038 §5 (Codex P1): et BESLUTNINGSOPPDRAG har ingen sak — det er hele
    # poenget med opprinnelsen. Den avsluttende bokføringen under er
    # M-37-veiens saksbokføring, og `unntak_historikk.unntak_id` er NOT NULL:
    # kjørt ubetinget døde HVER normal kvittering på et bestilt oppdrag i
    # basen, og rullet med seg statusskiftet og artefaktpromoteringen — altså
    # kunne et bestilt oppdrag aldri fullføres.
    #
    # Vi FØDER heller ingen sak her, i motsetning til de sene/motstridende
    # veiene: en kvittering i tide, innenfor fencing og frist, ER
    # normalveien. Evidensen ligger på oppdragsraden (kvittering, signatur,
    # resultathash, status) — som for enhver annen kvittering. En sak
    # opprettes når noe FAKTISK er et unntak, aldri som journalføring.
    if unntak_id is not None:
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


def _sikkerhetssak_kvittering(conn, tenant: str, unntak_id: int | None,
                              hendelse: str, detalj: dict, rid: str,
                              oppdrag_id: int | None = None) -> None:
    """Historikkrad for kvitteringsavvik. INGEN statusendring.

    v3-delta pkt. 3 er tydelig: et motstridende resultat skal ikke avgjøre
    noe. Det skal SES. En automatisk statusendring her ville betydd at den
    som klarer å sende to ulike kvitteringer bestemmer utfallet.

    038 §5 (port 24): et beslutningsoppdrag har ingen `unntak_id` — saken
    peker på oppdraget, ikke omvendt. Da hentes (eller fødes) den
    ikke-terminale sikkerhetssaken for oppdraget idempotent via
    `sikre_sak_for_oppdrag`, og avviket føres på DEN. Uten dette døde
    hele kvitteringskonflikt-transaksjonen på NOT NULL i historikken —
    altså nøyaktig hendelsen sikkerhetssporet finnes for.
    """
    if unntak_id is None:
        unntak_id = conn.execute(
            "SELECT sikre_sak_for_oppdrag(%s,%s,'sikkerhet',"
            "'kvitteringsport',%s)",
            (tenant, oppdrag_id, rid)).fetchone()[0]
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
        # 035: samme unntak som kvitteringen — modultokenet har ingen
        # scopes, og opplastingsretten er `kapabilitet_jti`-ens, ikke
        # tokenets. Artefaktkapabiliteten deles ut av claim-en, er bundet
        # til oppdrag + eiermodul + release/kontrakt/epoch, og innløses
        # mot `auth.rolle` under. En claim uten opplastingskapabilitet gir
        # ingen jti, og da stopper `innlos_artefaktkapabilitet` requesten.
        if not isinstance(auth, ModulAutentisert) \
                and "artifacts:upload" not in auth.scopes:
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

        # Innløs kapabiliteten — KUN for den holdende DEPLOYMENTEN.
        #
        # Codex P1: `auth.rolle` er modulens id, ikke deploymentens. En modul
        # kan ha flere levende deployments samtidig (staging og produksjon,
        # eller to releaser under hver sin kontraktversjon), hver med sitt
        # eget modultoken — og unntaket over slipper dem alle forbi
        # scope-porten. Uten miljø og release i innløsningen kunne en
        # staging-arbeider som fikk en jti utstedt til produksjons-
        # deploymenten levere rapporten, og API-et ville ført evidensen på
        # den releasen kapabiliteten bar: en attestering fra en deployment
        # som ikke autentiserte requesten. Med et legacy-api-token finnes
        # ingen autentisert deployment — NULL matcher da kun kapabiliteter
        # som selv er miljøløse (fail-closed begge veier).
        #
        # Codex P1, neste runde: og identiteten er ikke NOK — den er en
        # PÅSTAND fra en pre-auth-transaksjon som er lukket. Et nødstopp
        # som committet etterpå drepte tokenet, men `auth` husker det ikke.
        # Revalideringen står derfor før innløsningen, som på
        # kvitteringsveien.
        revalidering = _modultoken_revalidert(tjeneste, conn, auth, rid)
        if revalidering is not None:
            return revalidering
        d_miljo = getattr(auth, "miljo", None)
        d_release = getattr(auth, "release_id", None)
        bind = conn.execute(
            "SELECT tenant, oppdrag_id, release_id, kontraktversjon,"
            " kontrakt_hash, module_epoch, artefakttype"
            "  FROM innlos_artefaktkapabilitet(%s, %s, %s, %s)",
            (jti, auth.rolle, d_miljo, d_release)).fetchone()
        if bind is None:
            conn.rollback()
            tjeneste.logg.hendelse("kapabilitet_ugyldig", rid)
            return _feilsvar("kapabilitet_ugyldig", rid)
        (tenant, opp_id, release_id, kontraktversjon, kontrakt_hash,
         module_epoch, artefakttype) = bind

        # Tenant fra kapabiliteten. Server-beregnet JCS-hash + størrelse.
        sett_kontekst(conn, tenant, auth.aktor, rid)

        # PR-014c §8 pkt. 1: SKJEMAVALIDERING FØR KRYPTERING. Kapabiliteten
        # bærer artefakttypen; typen binder en skjema_hash; hashen slår opp
        # skjemaet. Ingen skjemarad → avvist (innhold ingen kan validere
        # tas ikke imot); brudd → avvist, med detaljene i sikkerhetsloggen
        # og aldri i svaret (rapportinnhold kan bære persondata).
        skjema = artefaktskjema.hent_skjema(conn, artefakttype)
        if skjema is None:
            conn.rollback()
            tjeneste.logg.hendelse("artefaktskjema_mangler", rid, tenant,
                                   art="drift", artefakttype=artefakttype)
            return _feilsvar("artefaktskjema_mangler", rid)
        skjemafeil = artefaktskjema.valider(skjema, rapport)
        if skjemafeil:
            conn.rollback()
            tjeneste.logg.hendelse("artefakt_skjemabrudd", rid, tenant,
                                   art="sikkerhet", artefakttype=artefakttype,
                                   antall=len(skjemafeil),
                                   forste=skjemafeil[0][:160])
            return _feilsvar("artefakt_skjemabrudd", rid)

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
        # Ett oppslag, én definisjon: `hent_aktiv_bak_loggreferanse` tolker
        # referansen og leser den aktive policyen den navngir — samme vei som
        # M-37 bruker når en reparasjon planlegges. Se den for hvorfor veien
        # ikke tar policylåsen mot sletting (Codex P1): unntakets loggrad ER
        # referansen `slett_ubrukt_policy` teller, og `revisjonslogg` er
        # append-only, så policyen bak den kan ikke slettes — verken i
        # vinduet mellom lesingen her og commit, eller noen gang senere.
        from .policyregister import hent_aktiv_bak_loggreferanse
        aktiv = hent_aktiv_bak_loggreferanse(
            conn, tenant, policy_ref[0] if policy_ref else None)
    except psycopg.Error:
        raise
    if aktiv is None:
        conn.rollback()
        return _feilsvar("policy_ukjent", rid)
    policy = aktiv[0]

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
    # MÅLHANDLINGEN kommer fra unntaket (`u.handling`, altså `policy_ref[1]`),
    # ikke fra policyreferansen: `les_policyref` leser (policy_id, VERSJON) ut
    # av den, aldri handlingen. Med referansens andre ledd sto det «1.0.0» der
    # en handlings-id skulle stå, oppslaget traff ingenting, og taket var
    # stille fraværende — altså en kontroll som så ut til å finnes og aldri
    # kunne fyre.
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
