"""Den EKTE kontrollmotoren (axe-core i headless Chromium, Playwright).

Kjøres av `Kommandomotor` (motor.py) — i browser-containeren på staging/
prod, direkte i et venv med playwright lokalt. Kontrakten er motorens
STDIN/STDOUT-grensesnitt og ingenting annet:

  stdin : {"mal_url", "kravsett", "omfang", "maks_sider"}   (payloaden)
  stdout: {"regelsett_versjon", "varighet_ms",
           "sider": [{"url","status"[,"bestilt_url"]}],
           "funn": [{"regel_id","alvorlighet","antall","eksempler"}],
           "blokkert": [{"vert","antall","art"}],
           "avkortet": [truffet, tak, verdi]}

Alt herfra er UBETRODD for controlleren (den validerer selv); det fritar
ikke motoren fra å være ærlig:

  * EGRESS ER LUKKET: kun målvertens origin slipper ut, og bare med
    LESENDE metoder (`LESEMETODER`) — kontrakten er `ekstern_lesing`, og
    samme origin er ikke det samme som ufarlig. Alle andre
    forespørsler blokkeres og TELLES ({vert, antall, art}) — det er
    tallene `dekningsbegrensninger` bygges av (port 18). Det gjelder
    ALLE kanalene, ikke bare de `route` ser: websockets avskjæres for
    seg, service workers blokkeres, og fremmede vertsnavn er
    uoppløselige i nettleseren. Ingen credentials: prosessen arver bare
    motor-allowlistens miljø.
  * MÅLET ER OFFENTLIG OG PINNET: vertsnavnet slås opp ÉN gang, hver
    adresse må være global, og både robots-henting og nettleser låses
    til den ene IP-en. Se `_pin_mal_ip`.
  * ROBOTS RESPEKTERES (port 20): gruppene for `User-agent: *` følges
    ved crawl, med RFC 9309-matching (`*`, `$`, Allow/Disallow-
    presedens); robots.txt 5xx → INGEN crawl — kun den eksplisitt
    bestilte `mal_url` kontrolleres (kunden har selv pekt på den; å
    crawle videre uten en lesbar robots er å gjette på lov). Reglene
    gjelder HVER navigasjon, ikke bare lenkene vi køer: et 30x, en
    `location.replace`, et `window.open` og en `<iframe src>` er alle
    sider vi ville hentet uten å ha filtrert dem, så vakten måler
    destinasjonen før den hentes. Bare den bestilte `mal_url` står
    utenfor — den er kundens eget valg.
  * TAKENE ER SYNLIGE (port 19): crawlen stopper på `maks_sider` og
    eksempellista på `MAKS_EKSEMPLER`, og BEGGE slår `avkortet` på —
    (truffet, tak, målt verdi), aldri en stille trunkering.

Axe-kilden er sha256-PINNET: en byttet CDN-fil gir exit != 0, aldri en
rapport bygget på ukjent regelverk. I containeren ligger fila bakt inn
(AXE_STI); nedlastingsveien finnes for lokal utvikling.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

AXE_VERSJON = "4.10.3"
AXE_URL = ("https://cdnjs.cloudflare.com/ajax/libs/axe-core/"
           f"{AXE_VERSJON}/axe.min.js")
#: sha256 over axe.min.js 4.10.3 — regnet av oss ved pinning, ikke lest
#: fra CDN-en (da hadde pinnen målt budbringeren med budbringerens tall).
AXE_SHA256 = "880970c081707360e64f34cea25ff91892f5bc95675b0776925b9709dd8a68bb"

#: WCAG 2.1 AA = 2.0 A + 2.0 AA + 2.1 A + 2.1 AA. Kun WCAG-tagger —
#: best-practice-regler er råd, ikke krav, og hører ikke hjemme i en
#: rapport som sier «kravsett: wcag21_aa».
KRAVSETT_TAGS = {"wcag21_aa": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]}

ALVORLIGHET = {"critical": "kritisk", "serious": "alvorlig",
               "moderate": "moderat", "minor": "lav"}

ART = {"stylesheet": "stilark", "font": "font", "script": "skript",
       "image": "bilde"}

#: Metodene en LESENDE kontroll trenger, og ikke én til (Codex P1).
#: Modulkontrakten klassifiserer hele operasjonen som `ekstern_lesing`:
#: ingen sideeffekt hos målet. Egressvakten målte bare ORIGIN, så en side
#: som selv kjørte `fetch(..., {method: "POST"})` eller
#: `navigator.sendBeacon` fikk skrive mot sitt eget nettsted — med
#: cookies satt under navigasjonen — og kunne dermed sende inn skjemaer
#: eller utløse handlinger i kundens navn mens vi «bare så på».
#: HEAD er med fordi den er GET uten kropp; OPTIONS trengs ikke når
#: skrivemetodene uansett er stengt.
LESEMETODER = frozenset({"GET", "HEAD"})

#: Rapportkontraktens egne tak (`rapport.MAKS_EKSEMPLER`/`MAKS_SELEKTOR`),
#: gjentatt her fordi motoren MÅ kappe før stdout: 500 funn à ti 200-tegns
#: eksempler er alene over artefaktets harde 1 MiB. Verdiene er IDENTISKE
#: med byggerens, og en test binder dem sammen — kappet motoren hardere enn
#: kontrakten, ville byggeren aldri fått se at noe ble kappet.
MAKS_EKSEMPLER = 10
MAKS_SELEKTOR = 200

#: AXES `target` ER EN STI, IKKE EN SELEKTORLISTE (Codex P2, runde 6).
#: Feltet er en liste med ETT ledd per tre elementet ligger i: en side med
#: `<iframe id="a">` gir `["#a", "button"]` — «finn `#a`, gå INN i den, og
#: der er `button`». Et element i en shadow root gir et NØSTET ledd,
#: `[["#vert", "button"]]`.
#:
#: Ledda ble skjøtet med `", "`. Resultatet var en syntaktisk gyldig
#: CSS-selektorliste som betyr noe helt annet — «#a ELLER button», begge
#: lest i toppdokumentet — og et nøstet ledd ble til Pythons egen
#: listerepresentasjon (`['#vert', 'button']`), som ikke er en selektor i
#: det hele tatt. Eksempelet i den promoterte rapporten pekte da på andre
#: elementer enn funnet, eller på ingen.
#:
#: Skillene sier derfor hvilken GRENSE som krysses, og ingen av dem kan
#: forveksles med CSS: `>>` finnes ikke i en selektor (`>` er barn, men
#: aldri to på rad), og `>>>` er den utgåtte shadow-piercing-kombinatoren
#: — samme betydning som her.
RAMMESKILLE = " >> "
SKYGGESKILLE = " >>> "

#: NETTLESERKONTEKSTEN SLIK DEN ATTESTERES (Codex P2). Serverkonteksten
#: fører hver kjøring som `locale: nb`, `viewport: 1280x800` og
#: `timezone: Europe/Oslo`, og de to første ble satt på kontekstobjektet
#: — den siste ikke. Chromium brukte da containerens egen tidssone, så en
#: side som rendrer innhold eller tilgjengelighetstilstand ut fra `Date`,
#: `Intl` eller lokal tid kunne bli undersøkt i et annet miljø enn det den
#: promoterte rapporten oppgir. Verdiene står her og bindes til
#: serverkonteksten av en test, samme grep som `MAKS_EKSEMPLER` og
#: `VERT_MONSTER`: to skrivemåter av samme påstand er ingen påstand.
LOCALE = "nb"
TIDSSONE = "Europe/Oslo"
VIEWPORT = {"width": 1280, "height": 800}

#: WEBRTC ER EN EGRESSKANAL UTENFOR HELE VAKTEN (Codex P1, runde 5).
#: `BrowserContext.route` ser HTTP, `route_web_socket` ser websockets — og
#: `RTCPeerConnection` er ingen av delene. En kontrollert side som peker
#: ICE/STUN/TURN på en RÅ IP sender UDP rett ut: resolverreglene
#: (`--host-resolver-rules`) treffer bare VERTSNAVN, og en IP-literal
#: trenger ikke DNS. Med `--network host` i den prosjekterte launcheren er
#: `127.0.0.1`, RFC1918 og skymetadata da innenfor rekkevidde — nøyaktig
#: det den lukkede egressen påstår at de ikke er.
#:
#: Kanalen stenges der den ÅPNES, i renderen, og på to lag:
#:
#:   1. Konstruktørene fjernes i HVERT dokument før sidens egne skript
#:      kjører (`add_init_script` gjelder alle rammer, også `about:blank`
#:      og iframes siden opprettes senere). Egenskapene settes
#:      IKKE-konfigurerbare, så en side kan ikke definere dem tilbake.
#:   2. Chromium-bryteren under, som forbud mot ikke-proxyet UDP. Ingen
#:      proxy er konfigurert, så det er et forbud, ikke en omdirigering.
#:      Laget finnes for det lag 1 ikke kan love alene: en realm som
#:      skulle rekke å bli lest før injeksjonen.
#:
#: Axe trenger ingen av API-ene — kontrollen kjører mot DOM-en slik den er
#: lastet, som for service workers og websockets. En side som må ha
#: sanntidskanalen for å rendre mister ikke rapporten; den mister
#: sanntidsdelen, og `dekningsbegrensninger` sier det.
#:
#: Det ENDELIGE laget hører hjemme på nettverksgrensen, og det står ikke
#: her med vilje: staging-fixturen er loopback-bundet, så launcheren må ha
#: `--network host` for i det hele tatt å nå fasit-nettstedet (se
#: `bygg.sh`). Så lenge den forutsetningen står, er renderen stedet.
WEBRTC_API = ("RTCPeerConnection", "webkitRTCPeerConnection",
              "RTCDataChannel", "RTCIceTransport", "RTCDtlsTransport",
              "RTCSctpTransport", "RTCCertificate")
WEBRTC_AV = (
    "for (const n of " + json.dumps(list(WEBRTC_API)) + ") {"
    "  try {"
    # `undefined`, ikke en getter som kaster: en side som gjør
    # funksjonstesting (`if (window.RTCPeerConnection)`) skal se en
    # nettleser UTEN WebRTC og degradere pent. En exception der ville
    # brutt sidens skript — og dermed DOM-en axe skal kontrollere.
    "    Object.defineProperty(window, n, {"
    "      value: undefined, configurable: false,"
    "      writable: false, enumerable: false});"
    "  } catch (e) { /* allerede ikke-konfigurerbar: like stengt */ }"
    "}"
)

#: Chromium-bryteren i lag 2. `--force-` overstyrer enhver policy i
#: imaget, og `disable_non_proxied_udp` uten proxy er et forbud: WebRTC får
#: da verken sende eller motta UDP.
WEBRTC_BRYTER = "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"

#: 2xx-statuser som IKKE etterlater et dokument (RFC 9110 §15.3.5/§15.3.6).
#: Chromium navigerer ikke i det hele tatt på dem — den blir stående der
#: den var — så det finnes ingen DOM å kontrollere.
TOMME_STATUS = frozenset({204, 205})


def _navigasjon_ok(status: int) -> bool:
    """Ga navigasjonen et dokument axe kan kontrollere? (Codex P2, runde 5)

    Porten var `status == 200`, og 200 er ikke det eneste vellykkede
    svaret. Et gyldig HTML-GET kan lovlig svare 201, 202 eller 203 —
    Playwright laster dokumentet som ellers — mens siden ble merket
    `feilet` og axe hoppet over den. Rapporten meldte da en
    navigasjonsfeil for en side som svarte helt normalt, i stedet for
    tilgjengelighetsfunnene den faktisk hadde.

    `TOMME_STATUS` holdes utenfor: der er `feilet` riktig utfall, for det
    er ingen side å kontrollere. Alt utenfor 2xx er som før."""
    return 200 <= status < 300 and status not in TOMME_STATUS


#: Navigasjonsfrist per side — romslig for et lokalt testnettsted, liten
#: mot claim-fristen. Motoren som helhet drepes uansett av Kommandomotors
#: vakthund; denne finnes for at ÉN hengende side skal gi `feilet` på den
#: siden i stedet for å spise hele vinduet.
SIDEFRIST_MS = 30_000


def _axe_kilde() -> str:
    """Axe-kilden, sha256-verifisert uansett hvor den kom fra."""
    sti = os.environ.get("AXE_STI")
    if sti:
        data = Path(sti).read_bytes()
    else:
        cache = Path(os.environ.get("TMPDIR", "/tmp")) / (
            f"axe-{AXE_VERSJON}.min.js")
        if cache.exists():
            data = cache.read_bytes()
        else:
            with urllib.request.urlopen(AXE_URL, timeout=30) as r:
                data = r.read()
            cache.write_bytes(data)
    faktisk = hashlib.sha256(data).hexdigest()
    if faktisk != AXE_SHA256:
        raise SystemExit(f"axe-kilden matcher ikke pinnen: {faktisk}")
    return data.decode("utf-8")


def _offentlig(ip) -> bool:
    """Er adressen en adresse på det OFFENTLIGE internettet?

    Alt annet — loopback, RFC1918, CGNAT, link-local (og dermed
    169.254.169.254, sky-metadataen), unique-local, multicast, reservert
    og «uspesifisert» — er en adresse motoren aldri har lov å nå. Et
    IPv4-mappet IPv6-svar (`::ffff:127.0.0.1`) pakkes ut først; ellers
    ville `is_loopback` sett på innpakningen i stedet for adressen."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _naabar(adresse: str, port: int) -> bool:
    """Har DENNE verten en rute til adressen? Ingen pakker sendes.

    `connect` på en UDP-socket sender ingenting — den ber bare kjernen
    velge kildeadresse og rute, og feiler umiddelbart (ENETUNREACH,
    EAFNOSUPPORT) når ingen finnes. Det er nøyaktig spørsmålet vi stiller,
    og det stilles uten å røre målet."""
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    familie = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        s = socket.socket(familie, socket.SOCK_DGRAM)
    except OSError:
        return False
    try:
        s.connect((adresse, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pin_mal_ip(vert: str, port: int, vertskart: dict[str, str]) -> str:
    """Den ENE adressen målet får nås på — eller SystemExit (Codex P1).

    Egressvakten sammenlignet bare URL-ens ORIGIN, og et origin sier
    ingenting om hvor navnet peker. En tenant som kontrollerer DNS for
    sitt eget verifiserte domene kunne derfor la det peke på 127.0.0.1
    eller 169.254.169.254 og få motoren — som kjører med `--network
    host` — til å hente vertens egne tjenester og skymetadata inn i en
    rapport tenanten selv laster ned. Verifiseringen av domenet hjelper
    ikke: den sier hvem som eier navnet, ikke hva navnet peker på nå.

    To ting må derfor stemme, og de er to forskjellige ting:

      * HVER adresse navnet løser til må være offentlig. Er ÉN forbudt,
        avvises HELE forespørselen — å bare hoppe over den forbudte og
        bruke resten ville gjort et angrep til et retry-spørsmål.
      * Oppslaget PINNES til én adresse, som bæres videre til både
        robots-hentingen (`_hent`) og nettleseren
        (`--host-resolver-rules`). Uten pinnen står DNS-rebinding igjen:
        navnet er offentlig i det vi kontrollerer det, og loopback i det
        Chromium slår det opp på nytt et halvsekund senere.

    MOTOR_VERTSKART er fixture-unntaket, og det er BEVISST eksplisitt:
    det syntetiske testnettstedet ER loopback. Kartet nevner nøyaktig de
    navnene staging-sjekklisten setter opp selv, settes aldri av
    driftens unit-filer, og når det ikke gjelder, gjelder regelen over.
    """
    if vert in vertskart:
        return vertskart[vert]
    try:
        oppslag = socket.getaddrinfo(vert, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise SystemExit(f"målet lot seg ikke slå opp: {vert}: {e}")
    adresser = []
    for post in oppslag:
        adresse = post[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(adresse)
        except ValueError:
            raise SystemExit(f"målet {vert} løste til noe som ikke er en"
                             f" IP-adresse: {adresse!r}")
        if not _offentlig(ip):
            raise SystemExit(
                f"målet {vert} peker på en ikke-offentlig adresse ({ip})"
                " — hele forespørselen avvises")
        adresser.append(adresse)
    if not adresser:
        raise SystemExit(f"målet lot seg ikke slå opp: {vert}")
    # ... OG DEN ENE MÅ VÆRE EN VI KAN NÅ (Codex P2). Pinnen tok `[0]`
    # blindt. Har et offentlig vertsnavn både AAAA og A, og resolveren
    # setter IPv6 først på en IPv4-only vert, ble BÅDE robots-hentingen og
    # Chromium låst til en adresse verten ikke har en rute til — en fullt
    # gyldig kontroll feilet, selv om det samme navnet hadde en offentlig
    # IPv4 rett ved siden av. Godkjenningen over står urørt: hver adresse
    # er alt kontrollert, så valget her er bare hvilken av de GODKJENTE vi
    # bruker.
    for adresse in adresser:
        if _naabar(adresse, port):
            return adresse
    # Ingen av dem lot seg rute. Da er `[0]` like godt som noe annet, og
    # feilen hører hjemme der den faktisk oppstår — i tilkoblingen, med
    # kjernens egen beskjed — ikke i en gjetning her.
    return adresser[0]


#: Så mye av et svar `_hent` leser. Robots.txt er en tekstfil på noen
#: kilobyte; taket finnes for at et uendelig svar ikke skal spise
#: kontrollvinduet. Google leser 500 KiB (RFC 9309 §2.5 tillater et tak),
#: men taket er bare forsvarlig når det SIER FRA — se `_Avkortet`.
LESETAK = 65_536


class _Avkortet(Exception):
    """Svaret var større enn `LESETAK` — vi har ikke lest hele det."""


#: Hvor mange omdirigeringer robots-hentingen følger. RFC 9309 §2.3.1.2
#: ber crawlere følge MINST fem påfølgende hopp; en kjede lengre enn det
#: er ingen kanonisering, og behandles som en robots vi ikke fikk lest.
#: Taket lukker samtidig omdirigeringsløkker (A -> B -> A).
ROBOTS_HOPP = 5


def _hent(url: str, pin_ip: str, tls_kontekst=None) -> tuple[int, str, str]:
    """GET mot `url`, men ALLTID mot den pinnede adressen.

    -> (status, kropp, `Location`). Plasseringen returneres fordi
    `_robots` følger en BEGRENSET omdirigeringskjede — hentingen selv
    følger ingenting, for hvert hopp må gjennom den samme
    origin-kontrollen som det første.

    Vertsnavnet beholdes til Host-header og SNI/sertifikatkontroll —
    bare selve TCP-tilkoblingen tvinges til `pin_ip`, slik at hentingen
    ikke gjør sitt EGET DNS-oppslag etter at `_pin_mal_ip` har godkjent
    et annet svar. `_create_connection` er `http.client`-instansens egen
    krok for nettopp dette.

    ET AVKORTET SVAR ER IKKE ET SVAR (Codex P1). Lesingen stoppet på
    `LESETAK` bytes og returnerte prefikset som om det var hele
    dokumentet. For robots.txt er det direkte farlig: hver `Disallow`
    etter grensen forsvant i stillhet, og crawleren hentet stier målet
    eksplisitt hadde forbudt — nettopp det taket ikke kan avgjøre noe
    om. Vi leser derfor ÉN byte forbi grensen, og finnes den, kaster vi
    `_Avkortet` i stedet for å tolke prefikset."""
    u = urllib.parse.urlsplit(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    if u.scheme == "https":
        conn = http.client.HTTPSConnection(u.hostname, port, timeout=10,
                                           context=tls_kontekst)
    else:
        conn = http.client.HTTPConnection(u.hostname, port, timeout=10)
    conn._create_connection = (
        lambda adr, tidsavbrudd, kilde: socket.create_connection(
            (pin_ip, adr[1]), tidsavbrudd, kilde))
    try:
        # Forespørselsmålet på origin-form (RFC 9112 §3.2.1). Query-en er
        # med fordi en omdirigering kan kanonisere til en sti som bærer
        # en — men den bygges HER og ikke med `_robotsti`: den formen er
        # RFC 9309s SAMMENLIGNINGSform for regler, og at de to i dag
        # skrives likt gjør dem ikke til samme ting. Delte vi funksjonen,
        # ville en endring gjort for robots-matchingen stille endret hva
        # vi ber serveren om.
        conn.request("GET", (u.path or "/") + (f"?{u.query}" if u.query
                                               else ""))
        svar = conn.getresponse()
        raa = svar.read(LESETAK + 1)
        if len(raa) > LESETAK:
            raise _Avkortet(url)
        plassering = (svar.getheader("location") or "").strip()
        return svar.status, raa.decode("utf-8", "replace"), plassering
    finally:
        conn.close()


def _robots(base: str, pin_ip: str, tls_kontekst=None
            ) -> tuple[list[str], bool]:
    """-> (disallow-regler for `*`, krype_lov).

    Alt annet enn 2xx og 4xx — 5xx, omdirigeringer og nettverksfeil —
    gir (.., False): ingen crawl. En robots vi ikke fikk LEST er ikke en
    robots som har sagt ja — fail-open her ville betydd at målets verste
    driftsøyeblikk er øyeblikket vi crawler mest. Kun et tydelig 4xx
    (robots finnes ikke) leses som «ingen uttalte begrensninger»
    (RFC 9309 §2.3.1.3).

    En robots STØRRE enn `LESETAK` går samme vei (Codex P1): `_Avkortet`
    er en av grunnene til å ikke ha lest robotsen, og den regnes ikke
    som noe annet enn de andre. Alternativet — å tolke prefikset — er
    fail-open i forkledning: reglene ligger i vilkårlig rekkefølge i
    fila, så det er tilfeldig hvilke forbud som havnet innenfor grensen,
    og en `Disallow` vi ikke leste blir til en sti vi crawlet.

    OMDIRIGERINGER FØLGES, BEGRENSET (Codex P2, runde 9). En vanlig
    301/302 til målets egen kanoniske robots-sti ble lest som «ikke
    lest», og et helt nettstedsoppdrag falt til én side selv om
    policyen lå rett rundt hjørnet. RFC 9309 §2.3.1.2 ber oss følge
    minst fem påfølgende hopp; vi følger `ROBOTS_HOPP` og faller
    fail-closed når kjeden er lengre.

    Hvert hopp må ligge på MÅLETS EGEN origin, og gjenbruker derfor
    pinnen fra `_pin_mal_ip`. Det er ikke en unødig innstramming: en
    annen origin er en annen vert, som verken er autorisert for dette
    oppdraget eller dekket av den adressekontrollen pinnen bærer — og
    en robots-henting er ikke stedet å åpne en ny egressvei. En
    omdirigering ut av origin, uten `Location`, eller til noe vi ikke
    kan lese, er derfor en robots vi ikke fikk lest."""
    url = base + "/robots.txt"
    mal_origin = _origin(urllib.parse.urlsplit(base))
    for _ in range(ROBOTS_HOPP + 1):
        try:
            status, tekst, plassering = _hent(url, pin_ip, tls_kontekst)
        except Exception:
            # `_Avkortet` er med her: en robots vi bare fikk BEGYNNELSEN av
            # er en robots vi ikke fikk lest.
            return [], False
        if 200 <= status < 300:
            return _parse_robots(tekst), True
        if 400 <= status < 500:
            return [], True
        if not (300 <= status < 400 and plassering):
            return [], False
        neste = urllib.parse.urljoin(url, plassering)
        neste_origin = _origin(urllib.parse.urlsplit(neste))
        if not mal_origin or neste_origin != mal_origin:
            return [], False
        url = neste
    return [], False


_PROSENTOKTETT = re.compile(r"%([0-9A-Fa-f]{2})")


def _robotsform(sti: str) -> str:
    """Stien på den formen robots-regler SAMMENLIGNES på (RFC 9309 §2.2.2).

    PROSENTKODINGEN NORMALISERES (Codex P1, runde 5). Standarden krever at
    begge sider bringes til samme form før de måles mot hverandre, og uten
    det er `Disallow: /privat/` en regel målet kan omgå ved å lenke til seg
    selv med en annen SKRIVEMÅTE: `/%70rivat/side` er samme ressurs for
    enhver server som avkoder ureserverte oktetter, men en rå
    strengsammenligning ser to ulike stier — og crawleren hentet den
    forbudte siden.

    Regelen er RFC 3986 §2.3/§6.2.2: en prosentkodet URESERVERT oktett
    (ALPHA / DIGIT / `-` `.` `_` `~`) BETYR tegnet selv og avkodes; alt
    annet forblir kodet, med heksene i store bokstaver. Reserverte tegn
    avkodes ALDRI — `%2F` er ikke en skillestrek, og `%2A` er ikke robots'
    `*`-metategn. Ikke-ASCII oktetter (`%C3%A9`) står også: hver av dem er
    over 0x7F og dermed utenfor det ureserverte settet.

    RÅ UTF-8 KODES FØRST (Codex P1, runde 9). Normaliseringen over rørte
    bare oktetter som ALLEREDE var prosentkodet, så et bokstavelig
    ikke-ASCII-tegn i fila sto urørt: `Disallow: /privat/æ` forble
    `/privat/æ`, mens nettleseren leverer den samme ressursen som
    `/privat/%C3%A6`. Regelen matchet da ingenting, og en eksplisitt
    forbudt side ble crawlet. Å normalisere den ene skrivemåten og ikke
    den andre er samme feil som å normalisere bare den ene siden: hvert
    tegn over 0x7F blir sine prosentkodede UTF-8-oktetter, slik at rå og
    kodet form møtes. ASCII røres ikke — robots' metategn `*` og `$` er
    ASCII, og `_regel` leser dem ETTER denne formen.

    Formen brukes på BEGGE sider — mønsteret i `_regel` og stien i
    `_tillatt` — for det er nettopp det som gjør den til en sammenligning."""
    def bytt(m: "re.Match") -> str:
        tegn = chr(int(m.group(1), 16))
        if tegn.isascii() and (tegn.isalnum() or tegn in "-._~"):
            return tegn
        return "%" + m.group(1).upper()
    if not sti.isascii():
        sti = "".join(
            t if t.isascii()
            else "".join(f"%{b:02X}" for b in t.encode("utf-8", "replace"))
            for t in sti)
    return _PROSENTOKTETT.sub(bytt, sti)


def _regel(tillat: bool, monster: str) -> tuple[bool, str, "re.Pattern"]:
    """Én robots-regel som (tillat, monster, kompilert matcher).

    RFC 9309 §2.2.2 gir stimønsteret to metategn, og BEGGE endrer hva
    regelen dekker: `*` matcher en vilkårlig sekvens, og `$` forankrer
    til slutten av stien. Leser man dem bokstavelig, som et prefiks, blir
    `Disallow: /privat/*.pdf$` en regel som ikke matcher NOE — og en
    eksplisitt forbudt sti ble crawlet (Codex P1). Resten av mønsteret er
    et rent prefiks, som før.

    Mønsteret normaliseres av `_robotsform` først — se den. Lengden som
    bærer presedensen i `_tillatt` er da den NORMALISERTE, altså den
    formen begge sider faktisk måles på."""
    monster = _robotsform(monster)
    anker = monster.endswith("$")
    kropp = monster[:-1] if anker else monster
    rx = re.compile(".*".join(re.escape(d) for d in kropp.split("*"))
                    + ("$" if anker else ""))
    return tillat, monster, rx


def _parse_robots(tekst: str) -> list[tuple[bool, str, "re.Pattern"]]:
    """Reglene som gjelder `User-agent: *` — RFC 9309-gruppert.

    GRUPPER, IKKE LINJER (Codex P1). En robots-gruppe er én eller flere
    SAMMENHENGENDE `User-agent`-linjer etterfulgt av reglene sine, og
    gruppa gjelder oss om NOEN av dem er `*`:

        User-agent: googlebot
        User-agent: *
        Disallow: /privat/

    Den forrige lesingen satte `gjelder = (verdi == "*")` per linje, så
    her vant den SISTE agentlinja — hadde `*` stått først, ble hele
    gruppa forkastet og /privat/ crawlet. Flere grupper for `*` slås
    sammen (§2.2.1).

    `Allow` leses nå også: uten den er `Disallow: /` + `Allow: /aapen/`
    et totalforbud vi ikke ble bedt om å følge. Presedensen er
    standardens, ikke vår egen — se `_tillatt`."""
    grupper: list[tuple[set[str], list]] = []
    forrige_var_agent = False
    for linje in tekst.splitlines():
        linje = linje.split("#", 1)[0].strip()
        if not linje or ":" not in linje:
            continue
        felt, verdi = (d.strip() for d in linje.split(":", 1))
        felt = felt.lower()
        if felt == "user-agent":
            if not forrige_var_agent or not grupper:
                grupper.append((set(), []))
            grupper[-1][0].add(verdi.lower())
            forrige_var_agent = True
            continue
        forrige_var_agent = False
        # En tom `Disallow:` er ingen begrensning, og en tom `Allow:` er
        # ingen tillatelse — begge er ingenting, og hører ikke hjemme som
        # et mønster som matcher alt.
        if felt in ("disallow", "allow") and verdi and grupper:
            grupper[-1][1].append(_regel(felt == "allow", verdi))
    return [r for agenter, regler in grupper if "*" in agenter
            for r in regler]


def _robotsti(p) -> str:
    """Stien en robots-regel måles mot: STI OG QUERY (RFC 9309 §2.2.2).

    `Disallow: /*?` er nettopp regelen som stenger de query-bærende sidene,
    og den kan ikke virke om vi måler den mot en sti uten query. Formen er
    ÉN funksjon fordi den brukes to steder — lenkefilteret i crawlen og
    omdirigeringsvakten — og to skrivemåter er to regler."""
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def _tillatt(sti: str, regler: list) -> bool:
    """RFC 9309 §2.2.2: MEST SPESIFIKKE regel vinner — lengste mønster,
    og `Allow` foran `Disallow` ved likt. Ingen treff = tillatt.

    Normaliseringen ligger HER og ikke hos kallerne: dette er det ene
    stedet en sti møter en regel, og en sammenligning der bare den ene
    siden er normalisert er ingen sammenligning (Codex P1). Mønstrene er
    normalisert i `_regel` med nøyaktig samme funksjon."""
    sti = _robotsform(sti)
    beste_lengde, beste_tillat = -1, True
    for tillat, monster, rx in regler:
        if rx.match(sti) and (len(monster) > beste_lengde
                              or (len(monster) == beste_lengde and tillat)):
            beste_lengde, beste_tillat = len(monster), tillat
    return beste_tillat


#: Standardporten per skjema — den som ER underforstått, og derfor aldri
#: skrives i et origin. `ws`/`wss` er med fordi kanoniseringen skal kjenne
#: HELE settet av skjemaer en kontrollert side kan be om (RFC 6455 §3: en
#: ws-URL har samme origin som http-URL-en med samme vert og port), ikke
#: fordi websocket-vakten slipper noen gjennom — den lukker alle.
STANDARDPORT = {"http": 80, "https": 443, "ws": 80, "wss": 443}
HTTP_SKJEMA = {"ws": "http", "wss": "https"}


#: SAMME mønster som `rapport._VERT`, som selv speiler
#: `rapportskjema._HOSTNAME`. Gjentatt her, ikke importert: motoren kjører
#: i browser-containeren uten plattformkoden på PYTHONPATH. En test binder
#: de to sammen, slik `rapport._VERT` alt er bundet til skjemaet.
VERT_MONSTER = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+\Z")

#: RFC 2606 §2 reserverer `.invalid` for navn som per definisjon ALDRI kan
#: slås opp. Et blokkert mål som ikke er et prikket vertsnavn plasseres der:
#: formen blir lesbar for rapportskjemaet, og ingen leser kan forveksle den
#: med et navn målet faktisk kontaktet.
UGYLDIG = "invalid"


def _rapportvert(raa: str) -> str:
    """Den blokkerte verten på en form rapporten kan BÆRE (Codex P2).

    `rapport.bygg` krever et prikket vertsnavn av hver blokkerte post, og
    en post som ikke matcher gir `Motorfeil` — altså `motor_avbrutt` for
    HELE kontrollen. En korrekt blokkert forespørsel til `localhost`, til
    en IPv6-literal eller til et navn uten punktum gjorde dermed en ellers
    vellykket kontroll om til et feilet oppdrag: vi mistet rapporten fordi
    egressvakten gjorde jobben sin. Den kontrollen skal ende i en
    dekningsbegrensning, som er nettopp det feltet finnes for.

    Verdien beholdes så langt den er representerbar, og plasseres ellers i
    `.invalid`:

      * IPv4-literalen er alt prikket og går urørt gjennom skjemaet.
      * IPv6-literalen skrives UTFOLDET, med `-` for `:`, under
        `.ipv6.invalid` — `ip.exploded`, ikke råstrengen: den komprimerte
        formen kan begynne eller slutte med `:` (`::1`), og en etikett kan
        ikke begynne med bindestrek. Den utfoldede formen er alltid åtte
        firetegns-grupper (39 tegn), altså både etikettlovlig og entydig.
        Klammene hører til URL-syntaksen, ikke til adressen.
      * Et navn uten punktum (`localhost`, `intern`) beholdes som første
        etikett under `.enkeltetikett.invalid`.
      * Alt annet uleselig — tomt, ulovlige tegn, for langt — blir
        `uleselig.blokkert.invalid`. Raden forsvinner ALDRI: at noe ble
        blokkert er sant også når vi ikke kan navngi hva.
    """
    n = (raa or "").strip().strip("[]").lower()
    try:
        ip = ipaddress.ip_address(n)
    except ValueError:
        ip = None
    if ip is not None and ip.version == 6:
        n = f"{ip.exploded.replace(':', '-')}.ipv6.{UGYLDIG}"
    elif ip is None and "." not in n:
        n = f"{n}.enkeltetikett.{UGYLDIG}"
    if not VERT_MONSTER.match(n) or len(n) > 253:
        return f"uleselig.blokkert.{UGYLDIG}"
    return n


def _origin(p) -> str:
    """Origin på KANONISK form — eller "" hvis URL-en ikke har noen.

    Egressvakten og lenkefilteret sammenlignet `f"{scheme}://{netloc}"` som
    RÅ STRENG, og netloc bærer tre ting som ikke skal telle med i et origin
    (Codex P2):

      * DEN UNDERFORSTÅTTE PORTEN. For målet `https://example.com` er
        lenken `https://example.com:443/produkt` samme origin etter
        nettleserens egne regler, men strengene er ulike. Lenken ble derfor
        forkastet FØR den nådde `oppdaget` — altså falt sider ut av
        crawlen samtidig som rapporten sa at ingenting var avkortet.
        Motsatt vei, i `vakt`, ble en helt legitim forespørsel til målets
        egen origin BLOKKERT og talt som en dekningsbegrensning.
      * STORE/SMÅ BOKSTAVER. Vertsnavn er case-insensitive.
      * BRUKERINFO (`user:pass@`), som ikke er en del av origin i det hele
        tatt.

    Et ugyldig portnummer gir "" — og "" matcher aldri målets origin, som
    alltid har en vert. En URL vi ikke kan lese er ikke en URL vi slipper
    gjennom."""
    skjema = (p.scheme or "").lower()
    skjema = HTTP_SKJEMA.get(skjema, skjema)
    vert = (p.hostname or "").lower()
    if not vert:
        return ""
    try:
        port = p.port
    except ValueError:
        return ""
    if ":" in vert:             # IPv6-literal — klammene hører til origin
        vert = f"[{vert}]"
    if port is None or port == STANDARDPORT.get(skjema):
        return f"{skjema}://{vert}"
    return f"{skjema}://{vert}:{port}"


def _normaliser_lenke(base_origin: str, side_url: str, href: str
                      ) -> str | None:
    """Absolutt URL på målets origin, uten fragment — ellers None.

    QUERY-EN BLIR MED (Codex P2). Den ble strøket ved å returnere None,
    altså ved å KASTE lenken, og det er to feil i ett: hver side et
    dynamisk nettsted ruter på query (`/produkt?id=42`) falt ut av
    crawlen, og siden URL-en aldri nådde `oppdaget`, sa rapporten
    samtidig at ingenting var avkortet. Et nettsted kunne dermed være så
    godt som ukontrollert i en rapport som ser komplett og ærlig ut.

    Query-en er BÆRENDE for sideidentitet — `/produkt?id=1` og
    `/produkt?id=2` er to sider — og skal likevel ikke lagres: den kan
    inneholde persondata. Det skillet finnes allerede ett nivå opp,
    `rapport._delt_url` bygger rapportformen uten query og
    identitetsformen med, så motoren kan crawle disse sidene uten at
    query-en havner i evidensen.

    FRAGMENTET faller fortsatt bort: det sendes aldri til serveren, så
    to URL-er som bare skiller seg der ber om samme dokument."""
    try:
        u = urllib.parse.urljoin(side_url, href)
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return None
    if _origin(p) != base_origin:
        return None
    return (f"{base_origin}{p.path or '/'}"
            + (f"?{p.query}" if p.query else ""))


def _selektorsti(mal, dybde: int = 0) -> str:
    """Axes `target` som én lesbar STI — se `RAMMESKILLE`/`SKYGGESKILLE`.

    Ytterste nivå krysser RAMMER, hvert nøstet nivå en shadow root. Sier
    axe noe vi ikke kan lese (et ledd som verken er streng eller liste),
    er et ærlig avbrutt oppdrag riktig utfall: kilden er pinnet på
    sha256, så formen kan ikke endre seg under oss uten at pinnen endres
    — samme grunn som for `ALVORLIGHET`."""
    if isinstance(mal, str):
        return mal
    if isinstance(mal, list):
        skille = RAMMESKILLE if dybde == 0 else SKYGGESKILLE
        return skille.join(_selektorsti(d, dybde + 1) for d in mal)
    raise SystemExit(f"axe ga en target vi ikke kan lese: {mal!r}")


def main() -> int:
    payload = json.loads(sys.stdin.read())
    mal_url = payload["mal_url"]
    tags = KRAVSETT_TAGS[payload["kravsett"]]
    maks_sider = int(payload["maks_sider"]) if (
        payload.get("omfang") == "nettsted") else 1

    p = urllib.parse.urlsplit(mal_url)
    # ÉN kanonisk origin, brukt av lenkefilteret OG av begge egressvaktene —
    # se `_origin`. Bygges den to steder, kan de to drive fra hverandre, og
    # da er «samme origin» to forskjellige spørsmål.
    origin = _origin(p)
    if not origin:
        raise SystemExit(f"mal_url har ingen lesbar origin: {mal_url!r}")
    axe_js = _axe_kilde()
    # MOTOR_TLS_USIKKER er STAGING-FIXTURENS bryter, aldri driftens: det
    # syntetiske testnettstedet kjører på loopback med selvsignert
    # sertifikat, og plattformen krever https-mål (normaliser_vertsnavn).
    # Unit-filene setter den ALDRI — et produksjonsmål med ugyldig TLS
    # skal feile, og gjør det. Bryteren svekker ikke egressvakten eller
    # robots: begge håndheves uansett.
    tls_usikker = os.environ.get("MOTOR_TLS_USIKKER") == "1"
    tls_kontekst = None
    if tls_usikker:
        import ssl
        tls_kontekst = ssl.create_default_context()
        tls_kontekst.check_hostname = False
        tls_kontekst.verify_mode = ssl.CERT_NONE
    # MOTOR_VERTSKART ("vert=ip[,vert=ip]") er fixture-søsteren til
    # TLS-bryteren: det syntetiske målet har et ekte vertsnavn (plattformen
    # krever det) men ingen DNS. Kartet gjelder KUN navnene det nevner —
    # all annen oppløsning er urørt — og settes aldri i drift.
    vertskart = dict(par.split("=", 1) for par in os.environ.get(
        "MOTOR_VERTSKART", "").split(",") if "=" in par)
    # KLOKKA STARTER FØR DET FØRSTE ARBEIDET MOT MÅLET (Codex P2).
    # `varighet_ms` er timingevidensen i den promoterte rapporten, og den
    # startet etter oppslaget, adressekontrollen OG robots-hentingen. Bare
    # robots kan alene bruke ti sekunder (`_hent`-fristen), og DNS kommer i
    # tillegg — arbeid som er like synlig utenfra som selve sidelastingen,
    # men som ikke fantes i tallet. Målt slik understøtter evidensen
    # systematisk hvor lenge motoren faktisk holdt på.
    #
    # `_axe_kilde()` over er UTENFOR med hensikt: den leser en innbakt fil
    # (eller en lokal cache) og rører aldri målet.
    start = time.monotonic()
    # ÉTT oppslag, godkjent én gang, brukt overalt — se `_pin_mal_ip`.
    mal_vert = p.hostname or ""
    mal_pin = _pin_mal_ip(mal_vert, p.port or (443 if p.scheme == "https"
                                               else 80), vertskart)
    # ROBOTS HENTES BARE NÅR VI SKAL CRAWLE (Codex P2). Kallet var
    # ubetinget, så HVER `enkeltside`-kontroll sendte en ekstra GET mot
    # `/robots.txt` — en forespørsel som per definisjon ikke kunne endre
    # noe (`maks_sider == 1`, ingen lenker følges). En bestilling
    # autorisert og bokført som ÉN sides inspeksjon ble dermed to
    # utenfra synlige treff på kundens nettsted, mot en sti kunden aldri
    # pekte på.
    disallow: list = []
    krype_lov = True
    #: Det BESTILTE taket, tatt vare på før robots får lov å senke det —
    #: se `robots_stengte` nedenfor.
    bestilt_maks = maks_sider
    robots_stengte = False
    if maks_sider > 1:
        disallow, krype_lov = _robots(origin, mal_pin, tls_kontekst)
        if not krype_lov:
            maks_sider = 1      # robots 5xx: kun den bestilte siden
            robots_stengte = True
    #: Crawler vi i det hele tatt? Robots-reglene styrer hvilke sider vi
    #: HENTER utover den bestilte, så de gjelder nøyaktig i denne modusen —
    #: både for lenkene vi køer, omdirigeringene vi følger og navigasjonene
    #: målets egne skript setter i gang.
    krype = maks_sider > 1
    #: Den BESTILTE siden, i kanonisk form. Det er den ene URL-en robots
    #: ikke måles mot i vakten: kunden pekte selv på den. Formen er
    #: lenkefilterets egen, så sammenligningen ikke kan gli på en
    #: standardport eller et fragment — se `_normaliser_lenke`.
    mal_kanonisk = _normaliser_lenke(origin, mal_url, mal_url) or mal_url

    blokkert: dict[tuple[str, str], int] = {}
    funn: dict[str, dict] = {}
    sider: list[dict] = []
    #: Den LENGSTE selektoren som ble kappet av `MAKS_SELEKTOR`, eller 0.
    #: Se kappingen nede i funnløkka — det tredje taket, og det eneste
    #: eksempelregnskapet ikke kan se.
    selektor_avkortet = 0
    #: SIDEBUDSJETTET GJELDER HVERT DOKUMENT (Codex P1, runde 5). `besokt`
    #: og `maks_sider` talte bare URL-er vi selv tok ut av KØEN, og en side
    #: trenger ingen kø for å hente dokumenter: hver `<iframe src=…>`, hvert
    #: `window.open` er en navigasjon til, og axe kontrollerer rammene med.
    #: En `enkeltside`-kontroll av en side med hundre samme-origin-iframes
    #: hentet dermed hundreogén dokumenter fra kundens nettsted — uten at
    #: ett av dem sto i `sider`, og med `avkortet.truffet: false` på toppen.
    #: Både trafikkbudsjettet bestillingen autoriserte og dekningen
    #: rapporten erklærer var da påstander uten en grense bak seg.
    #:
    #: BUDSJETTET TELLER LASTINGER, IKKE URL-ER (Codex P1, runde 6). Første
    #: utgave førte regnskapet som et SETT av kanoniske dokument-URL-er, og
    #: lot en navigasjon til noe vi alt hadde hentet gå gratis. Men det er
    #: hentingen som koster — målets båndbredde, vår tid, kundens
    #: autoriserte trafikk — og en side kan navigere til sin EGEN URL så
    #: mange ganger den vil: hundre `<iframe src="./">`, et `window.open`
    #: i løkke. Settet gjorde da nøyaktig det taket skulle hindre til noe
    #: gratis, og `dokument_nektet` forble null mens rapporten sa at
    #: ingenting var avkortet.
    #:
    #: Køens egne sider går gjennom den samme vakten og fyller de samme
    #: plassene — budsjettet er ETT, slik bestillingen ba om `maks_sider`
    #: og ikke «maks_sider pluss det målet finner på».
    dokument_lastinger = 0
    #: Antall dokumentnavigasjoner budsjettet stengte. Bærer avkortingen
    #: nederst: en grense som ikke sier fra er en stille utelatelse.
    dokument_nektet = 0

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        # NETTLESEREN FÅR ÉN ADRESSE OG INGEN ANDRE (Codex P1). Målnavnet
        # låses til adressen `_pin_mal_ip` alt har godkjent, så Chromium
        # ikke kan slå det opp på nytt og få loopback tilbake
        # (DNS-rebinding). `MAP * ~NOTFOUND` står sist og gjør ethvert
        # ANNET vertsnavn uoppløselig: egressvakten under er en
        # route-avskjæring, og den ser ikke alt nettleseren kan finne på å
        # åpne. Rekkefølgen bærer regelen — Chromium bruker første treff,
        # så de eksplisitte MAP-ene vinner over catch-all-en.
        resolverkart = dict(vertskart)
        resolverkart.setdefault(mal_vert, mal_pin)
        regler = ", ".join(f"MAP {v} {ip}"
                           for v, ip in sorted(resolverkart.items()))
        chrom_args = ["--disable-dev-shm-usage",
                      f"--host-resolver-rules={regler}, MAP * ~NOTFOUND",
                      # Lag 2 mot WebRTC — se `WEBRTC_BRYTER`. Resolver-
                      # reglene over stopper fremmede VERTSNAVN; en rå
                      # IP-literal i en ICE-kandidat trenger ikke DNS.
                      WEBRTC_BRYTER]
        browser = pw.chromium.launch(args=chrom_args)
        # SERVICE WORKERS BLOKKERES (Codex P1). En registrert service
        # worker svarer på sidens forespørsler UTENFOR sidens egen
        # route-avskjæring, og kan selv hente hva den vil — altså en vei
        # rundt egressvakten, i en motor hvis hele kontrakt er at egressen
        # er lukket. Axe trenger dem ikke: kontrollen kjører mot DOM-en
        # slik den er lastet.
        ctx = browser.new_context(viewport=dict(VIEWPORT),
                                  locale=LOCALE,
                                  timezone_id=TIDSSONE,
                                  service_workers="block",
                                  ignore_https_errors=tls_usikker)
        # Lag 1 mot WebRTC — se `WEBRTC_AV`. Skriptet kjøres i HVERT nytt
        # dokument i konteksten, hovedramme som iframe, før sidens egne
        # skript får se en realm.
        ctx.add_init_script(WEBRTC_AV)

        def tell(vert: str, art: str) -> None:
            # `_rapportvert` er porten mot rapportskjemaet: en blokkering
            # skal bli en dekningsbegrensning, aldri et avbrutt oppdrag.
            n = _rapportvert(vert)
            blokkert[(n, art)] = blokkert.get((n, art), 0) + 1

        def vakt(route):
            nonlocal dokument_nektet, dokument_lastinger
            req = route.request
            u = urllib.parse.urlsplit(req.url)
            if _origin(u) != origin:
                tell(u.hostname or u.netloc,
                     ART.get(req.resource_type, "annet"))
                route.abort("blockedbyclient")
                return
            # KONTROLLEN LESER, DEN SKRIVER IKKE (Codex P1). Se
            # `LESEMETODER`: samme origin er ikke det samme som ufarlig.
            if req.method.upper() not in LESEMETODER:
                tell(u.hostname or u.netloc,
                     ART.get(req.resource_type, "annet"))
                route.abort("blockedbyclient")
                return
            # ROBOTS GJELDER HVER NAVIGASJON (Codex P1). Filteret sto først
            # bare på lenkene VI la i køen, og et 30x er ikke en lenke: en
            # tillatt `/gaa` som svarer 301 til `Disallow: /privat/side`
            # fortsatte her fordi målet var samme origin, og axe kjørte mot
            # den forbudte siden. Etter at rapporten begynte å navngi den
            # landede URL-en, ble den til og med promotert evidens for en
            # side robots eksplisitt stengte — altså en rett vei rundt
            # porten, åpnet av målet selv.
            #
            # ALLE NAVIGASJONER, IKKE BARE OMDIRIGERINGENE (Codex P1, runde
            # 4). Neste utgave målte bare forespørsler med
            # `redirected_from`, og en navigasjon trenger ingen
            # omdirigering for å oppstå: `location.replace('/privat/side')`,
            # et `window.open`, en `<iframe src=…>` eller en `<meta
            # refresh>` er alle navigasjoner UTEN forgjenger. Hver av dem
            # hentet den forbudte siden, og for hovedrammens del kunne axe
            # ende opp med å kontrollere den — altså samme vei rundt porten,
            # åpnet med et annet verktøy.
            #
            # UNNTAKET er nøyaktig ÉN URL: den bestilte `mal_url`. Den er
            # kundens eget valg, ikke noe vi fant ved å følge nettstedet, og
            # den har alltid gjeldt. Alt annet måles — lenkene vi køer er
            # alt `_tillatt`-filtrert, så for dem er kontrollen her et
            # gjentak, ikke en ny grense. Subressurser (bilder, stilark) er
            # ikke sider vi crawler, og `is_navigation_request()` holder dem
            # utenfor.
            if (krype and req.is_navigation_request()
                    and _normaliser_lenke(origin, req.url, req.url)
                    != mal_kanonisk
                    and not _tillatt(_robotsti(u), disallow)):
                # Blokkeringen er en DEKNINGSBEGRENSNING, ikke et avbrudd:
                # siden faller ut av crawlen, og rapporten sier hvorfor.
                tell(u.hostname or u.netloc, "annet")
                route.abort("blockedbyclient")
                return
            # BUDSJETTET TELLES ETTER ROBOTS (Codex P1, runde 5). En
            # navigasjon robots alt har stengt skal ikke koste en plass —
            # den ble aldri hentet. Se `dokument_lastinger` for hvorfor
            # grensen gjelder hver LASTING og ikke bare køen.
            #
            # `redirected_from` holdes UTENFOR: et 30x er ikke et dokument
            # til, det er den samme navigasjonen som fortsetter. Talte vi
            # dem, ville en `/side` → `/side/` spist to plasser av et
            # budsjett bestillingen ga for én side.
            #
            # Subressurser (bilder, stilark, skript) er ikke dokumenter og
            # har aldri vært i denne grensen — `is_navigation_request()`
            # holder dem utenfor, som i robots-porten over.
            #
            # HVER LASTING TELLER, OGSÅ AV EN URL VI HAR SETT (Codex P1,
            # runde 6). Dedupliseringen mot et sett av kanoniske URL-er ga
            # målet en gratis vei rundt hele grensen: en side som åpner
            # rammer mot sin egen adresse hentet ubegrenset mange
            # dokumenter, med `dokument_nektet == 0` og `avkortet: false`
            # øverst. Det er lastingen budsjettet finnes for å begrense.
            if req.is_navigation_request() and req.redirected_from is None:
                if dokument_lastinger >= maks_sider:
                    dokument_nektet += 1
                    tell(u.hostname or u.netloc, "annet")
                    route.abort("blockedbyclient")
                    return
                dokument_lastinger += 1
            route.continue_()

        # WEBSOCKETS LUKKES HELT (Codex P1). `BrowserContext.route` ser
        # bare HTTP-forespørsler; en `new WebSocket("wss://…")` på en
        # kontrollert side gikk rett forbi vakten til et hvilket som helst
        # eksternt eller internt endepunkt. Resolverregelen over stopper
        # fremmede VERTSNAVN, men ikke en rå IP-literal — så kanalen må
        # også lukkes her, og blokkeringen TELLES som alt annet blokkert.
        #
        # SAMME ORIGIN VAR IKKE NOK (Codex P1, runde 4). Første utgave
        # koblet gjennom hver websocket til målets egen origin. Men
        # `LESEMETODER` er kontraktens skille mellom å lese og å skrive, og
        # en websocket-ramme HAR ingen HTTP-metode: kanalen er toveis fra
        # første byte. En kontrollert side kunne dermed sende
        # tilstandsendrende protokollmeldinger til `wss://<mål>/…` — med
        # cookies satt under navigasjonen — og omgå nøyaktig den
        # begrensningen HTTP-vakten håndhever, i en motor hvis handling er
        # klassifisert `ekstern_lesing`.
        #
        # Axe trenger ingen websocket: kontrollen kjører mot DOM-en slik
        # den er lastet, som for service workers. En side som må ha én for
        # å rendre, mister ikke rapporten — den mister sanntidsdelen, og
        # rapporten SIER det gjennom `dekningsbegrensninger`.
        def vakt_ws(ws):
            u = urllib.parse.urlsplit(ws.url)
            tell(u.hostname or u.netloc, "annet")
            ws.close()

        ctx.route("**/*", vakt)
        if not hasattr(ctx, "route_web_socket"):
            # Uten avskjæringen er «egressen er lukket» en påstand vi ikke
            # kan holde. Da er et ærlig avbrutt oppdrag riktig utfall, ikke
            # en rapport bygget med en åpen kanal (playwright >= 1.48).
            raise SystemExit("playwright mangler route_web_socket —"
                             " egressvakten kan ikke håndheves")
        ctx.route_web_socket("**/*", vakt_ws)
        page = ctx.new_page()
        page.set_default_timeout(SIDEFRIST_MS)

        ko: list[str] = [mal_url]
        # `oppdaget` er tellingen `avkortet.verdi` rapporterer, så den skal
        # holde KANONISKE URL-er: er `mal_url` skrevet med standardporten
        # eller stor forbokstav i verten, ville den ellers ligget der i to
        # former og gjort tellingen én for høy.
        oppdaget = {mal_kanonisk}
        besokt = 0
        while ko and besokt < maks_sider:
            url = ko.pop(0)
            besokt += 1
            try:
                svar = page.goto(url, wait_until="load")
                ok = svar is not None and _navigasjon_ok(svar.status)
            except Exception:
                ok = False
            # SIDEN ER DEN VI FAKTISK LANDET PÅ (Codex P1). `page.goto`
            # følger omdirigeringer, og axe kjører mot det ENDELIGE
            # dokumentet — men rapporten fikk den BESTILTE URL-en. En
            # `enkeltside`-kontroll av `/gammel` som svarer 301 til `/ny`
            # ble dermed promotert som evidens for at `/gammel` var
            # undersøkt, og bindingen mot bestillingen holdt: URL-en var
            # jo den bestilte. Det er nettopp da en påstand er farlig.
            #
            # Samme URL bærer lenkeoppløsningen lenger nede: relative
            # href-er i det endelige dokumentet er relative til DET, ikke
            # til adressen vi spurte om, så `/gammel` → `/ny/` + `a.html`
            # ellers ble crawlet som `/a.html` — en annen side enn den
            # dokumentet faktisk lenker til.
            landet = _normaliser_lenke(origin, url, page.url) if ok else None
            faktisk = landet or url
            post = {"url": faktisk, "status": "ok" if ok else "feilet"}
            # OMDIRIGERINGEN ATTESTERES (Codex P1). Å navngi den landede
            # siden var riktig, men det etterlot bindingen i `rapport.bygg`:
            # for `enkeltside` KREVER den at første sides identitet er den
            # bestilte, så hver eneste kontroll av en URL som omdirigerer —
            # `/side` → `/side/` er ikke et sjeldent tilfelle, det er
            # normalen på halve nettet — ble `motor_avbrutt` uten at noen
            # rapport ble promotert.
            #
            # Motoren SIER derfor hvor den ble sendt fra, og bindingen
            # leser det. Feltet reiser bare fra motoren til byggeren: den
            # promoterte rapporten navngir fortsatt siden vi faktisk
            # kontrollerte, og rapportskjemaet er urørt.
            if faktisk != url:
                post["bestilt_url"] = url
            sider.append(post)
            if not ok:
                continue
            # Omdirigeringsmålet er en side vi HAR sett; uten dette kunne
            # den dukke opp som «ny» lenger ute i crawlen og bli kontrollert
            # en gang til på taket sin bekostning.
            oppdaget.add(faktisk)
            page.evaluate(axe_js)
            res = page.evaluate(
                "tags => axe.run(document, {runOnly:"
                " {type: 'tag', values: tags}})", tags)
            for v in res.get("violations", []):
                rid = v["id"]
                # UKJENT ALVORLIGHET ER IKKE «lav» (Codex P2). Fallbacken
                # gjorde manglende data (`impact: null`) og en verdi vi
                # ikke kjenner om til en KONKRET påstand nederst på
                # skalaen — skjemagyldig, promoterbar, og en understøtting
                # av funnet i den ferdige rapporten. Kilden er pinnet på
                # sha256, så settet av lovlige impact-verdier er kjent og
                # kan ikke endre seg under oss uten at pinnen endres; et
                # svar utenfor settet er derfor utdata vi ikke kan lese,
                # og da er et ærlig feilet oppdrag riktig utfall.
                impact = v.get("impact")
                if impact not in ALVORLIGHET:
                    raise SystemExit(
                        f"axe ga en alvorlighet vi ikke kjenner for {rid}:"
                        f" {impact!r}")
                f = funn.setdefault(rid, {
                    "regel_id": rid,
                    "alvorlighet": ALVORLIGHET[impact],
                    "antall": 0, "eksempler": []})
                for node in v.get("nodes", []):
                    f["antall"] += 1
                    # `antall` teller ALLE nodene, `eksempler` bærer de
                    # første `MAKS_EKSEMPLER`. Differansen mellom de to er
                    # avkortingssignalet nederst — se `avkortet`.
                    if len(f["eksempler"]) < MAKS_EKSEMPLER:
                        # STIEN BEHOLDER SIN STRUKTUR (Codex P2, runde 6):
                        # `", "` gjorde en traversering om til en
                        # selektorliste, og et nøstet ledd til en
                        # Python-repr. Se `_selektorsti`.
                        sel = _selektorsti(node.get("target", []))
                        # Samme kutt byggeren gjør på nytt, med samme tall:
                        # selektorlengden er kontraktens grense, ikke en
                        # kapping motoren finner på bak byggerens rygg.
                        #
                        # OG DET SIER FRA (Codex P2). Kuttet er det TREDJE
                        # taket i motoren, og det eneste som ikke ble målt:
                        # for et funn med én dyp node var både `antall` og
                        # `len(eksempler)` 1, så eksempelregnskapet under
                        # så ingenting — mens eksempelet i den promoterte
                        # rapporten var en avhogd, ofte syntaktisk ødelagt
                        # selektor under påstanden `avkortet: false`. En
                        # leser som skal finne igjen elementet har da fått
                        # en peker som ikke peker.
                        if len(sel) > MAKS_SELEKTOR:
                            selektor_avkortet = max(selektor_avkortet,
                                                    len(sel))
                        f["eksempler"].append(sel[:MAKS_SELEKTOR])
            if maks_sider > 1:
                # LENKEN LØSES SLIK NETTLESEREN LØSER DEN (Codex P2, runde
                # 5). `getAttribute('href')` gir attributtet RÅTT, og et
                # dokument med `<base href="/docs/">` løser sine relative
                # lenker mot BASEN — ikke mot dokumentets egen adresse. En
                # `guide.html` som brukeren besøker på `/docs/guide.html`
                # ble derfor køet som `/guide.html`: crawlen kontrollerte
                # en side ingen lenket til, utelot den som FANTES, og
                # rapporten kunne samtidig si at ingenting var avkortet.
                #
                # `document.baseURI` er nøyaktig det svaret — den er
                # dokumentets adresse når ingen `<base>` finnes, så
                # `faktisk`-oppløsningen fra runde 2 er bevart, og basen
                # når den gjør det. `new URL(...)` og ikke `e.href`:
                # `a[href]` treffer også SVG-ankere, og der er `href` en
                # `SVGAnimatedString`, ikke en streng. En href som ikke lar
                # seg løse (`javascript:` med rusk, tomt attributt) blir
                # tom streng og faller ut i `_normaliser_lenke`.
                for href in page.eval_on_selector_all(
                        "a[href]", "els => els.map(e => { try {"
                        " return new URL(e.getAttribute('href'),"
                        " document.baseURI).href } catch (x)"
                        " { return '' } })"):
                    lenke = _normaliser_lenke(origin, faktisk, href or "")
                    if not lenke or lenke in oppdaget:
                        continue
                    # Robots matcher mot STI OG QUERY — se `_robotsti`.
                    d = urllib.parse.urlsplit(lenke)
                    if _tillatt(_robotsti(d), disallow):
                        oppdaget.add(lenke)
                        ko.append(lenke)
        browser.close()

    # AVKORTINGEN DEKKER ALLE TAKENE (Codex P2). `truffet` var `bool(ko)`
    # alene, altså kun crawltaket — men eksempeltaket kapper OGSÅ evidens,
    # og det gjorde det stille: for en regel med mer enn `MAKS_EKSEMPLER`
    # feilende noder forkastet motoren eksempler den ALT hadde observert,
    # mens rapporten meldte `avkortet.truffet: false`. Byggerens egen
    # eksempeltelling kunne ikke fange det: den ser bare den kappede lista,
    # så tallet er per definisjon på taket, aldri over.
    #
    # Crawltaket har forsett når begge er truffet: det sier at hele SIDER
    # mangler, mens eksempeltaket sier at én regel har flere forekomster
    # enn de viste. `truffet` er sann uansett hvilket av dem det var.
    sider_avkortet = bool(ko)
    # Det største nodeantallet ETT funn hadde utover eksempeltaket — det er
    # tellingen taket faktisk ble målt mot, slik `rapport.bygg` gjør det.
    eksempler_avkortet = max(
        (f["antall"] for f in funn.values()
         if f["antall"] > len(f["eksempler"])), default=0)
    if sider_avkortet:
        avkortet = [True, maks_sider, len(oppdaget)]
    elif dokument_nektet:
        # DOKUMENTBUDSJETTET SIER FRA (Codex P1, runde 5). Køen kan være
        # tom — en `enkeltside`-kontroll har aldri noe i den — mens
        # budsjettet likevel stengte rammer siden ville ha hentet. Uten
        # denne grenen ble den stengningen usynlig, og rapporten sa «alt
        # kom med» om en kontroll der målet selv pekte på mer.
        #
        # Verdien er det målet ba om, taket det vi ga: `dokument_lastinger`
        # er plassene vi brukte, `dokument_nektet` de vi nektet.
        avkortet = [True, maks_sider, dokument_lastinger + dokument_nektet]
    elif robots_stengte:
        # EN ULESELIG ROBOTS ER OGSÅ EN AVKORTING (Codex P2). Da robots
        # svarte 5xx eller ikke lot seg hente, ble et bestilt
        # NETTSTED-oppdrag stille til en enkeltsidekontroll: lenkeuttrekket
        # er av, køen blir tom, og `sider_avkortet` er derfor usann. Uten
        # dette meldte rapporten `avkortet.truffet: false` — «alt kom med»
        # — for en kontroll som dekket én av inntil femti sider, og
        # rapporten bærer ikke det bestilte `omfang` noe annet sted. Et
        # forbigående driftsminutt hos kunden kunne dermed gi PROMOTERT
        # evidens som ser komplett ut.
        #
        # Taket er det vi faktisk kunne besøke (1), verdien er det som ble
        # bestilt: trippelen sier «kappet ved 1 av `bestilt_maks`».
        avkortet = [True, maks_sider, bestilt_maks]
    elif eksempler_avkortet:
        avkortet = [True, MAKS_EKSEMPLER, eksempler_avkortet]
    elif selektor_avkortet:
        # SELEKTORKUTTET ER OGSÅ EN AVKORTING (Codex P2). Rekkefølgen er
        # skadens: mangler hele SIDER er det mer enn at ett funn viser
        # færre forekomster, og begge deler er mer enn at ett eksempel
        # peker upresist. Sist betyr ikke uviktig — uten grenen her var
        # dette taket det ene som kunne slå til uten at noe felt sa fra.
        avkortet = [True, MAKS_SELEKTOR, selektor_avkortet]
    else:
        avkortet = [False, None, None]
    resultat = {
        "regelsett_versjon": f"axe-core-{AXE_VERSJON}",
        "varighet_ms": int((time.monotonic() - start) * 1000),
        "sider": sider,
        "funn": sorted(funn.values(), key=lambda f: f["regel_id"]),
        "blokkert": [{"vert": v, "antall": n, "art": a}
                     for (v, a), n in sorted(blokkert.items())],
        "avkortet": avkortet,
    }
    json.dump(resultat, sys.stdout)
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
