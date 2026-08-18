"""Den EKTE kontrollmotoren (axe-core i headless Chromium, Playwright).

Kjøres av `Kommandomotor` (motor.py) — i browser-containeren på staging/
prod, direkte i et venv med playwright lokalt. Kontrakten er motorens
STDIN/STDOUT-grensesnitt og ingenting annet:

  stdin : {"mal_url", "kravsett", "omfang", "maks_sider"}   (payloaden)
  stdout: {"regelsett_versjon", "varighet_ms",
           "sider": [{"url","status"}],
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
    gjelder OGSÅ omdirigeringsmål: et 30x er ingen lenke vi filtrerte,
    så vakten måler destinasjonen før den hentes.
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
    return adresser[0]


def _hent(url: str, pin_ip: str, tls_kontekst=None) -> tuple[int, str]:
    """GET mot `url`, men ALLTID mot den pinnede adressen.

    Vertsnavnet beholdes til Host-header og SNI/sertifikatkontroll —
    bare selve TCP-tilkoblingen tvinges til `pin_ip`, slik at hentingen
    ikke gjør sitt EGET DNS-oppslag etter at `_pin_mal_ip` har godkjent
    et annet svar. `_create_connection` er `http.client`-instansens egen
    krok for nettopp dette."""
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
        conn.request("GET", u.path or "/")
        svar = conn.getresponse()
        return svar.status, svar.read(65536).decode("utf-8", "replace")
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
    (RFC 9309 §2.3.1.3)."""
    try:
        status, tekst = _hent(base + "/robots.txt", pin_ip, tls_kontekst)
    except Exception:
        return [], False
    if not 200 <= status < 300:
        return [], 400 <= status < 500
    return _parse_robots(tekst), True


def _regel(tillat: bool, monster: str) -> tuple[bool, str, "re.Pattern"]:
    """Én robots-regel som (tillat, monster, kompilert matcher).

    RFC 9309 §2.2.2 gir stimønsteret to metategn, og BEGGE endrer hva
    regelen dekker: `*` matcher en vilkårlig sekvens, og `$` forankrer
    til slutten av stien. Leser man dem bokstavelig, som et prefiks, blir
    `Disallow: /privat/*.pdf$` en regel som ikke matcher NOE — og en
    eksplisitt forbudt sti ble crawlet (Codex P1). Resten av mønsteret er
    et rent prefiks, som før."""
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
    og `Allow` foran `Disallow` ved likt. Ingen treff = tillatt."""
    beste_lengde, beste_tillat = -1, True
    for tillat, monster, rx in regler:
        if rx.match(sti) and (len(monster) > beste_lengde
                              or (len(monster) == beste_lengde and tillat)):
            beste_lengde, beste_tillat = len(monster), tillat
    return beste_tillat


#: Standardporten per skjema — den som ER underforstått, og derfor aldri
#: skrives i et origin. `ws`/`wss` står her fordi websocket-vakten måler
#: samme origin som HTTP-vakten (RFC 6455 §3: en ws-URL har samme origin
#: som http-URL-en med samme vert og port).
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
    if maks_sider > 1:
        disallow, krype_lov = _robots(origin, mal_pin, tls_kontekst)
        if not krype_lov:
            maks_sider = 1      # robots 5xx: kun den bestilte siden
    #: Crawler vi i det hele tatt? Robots-reglene styrer hvilke sider vi
    #: HENTER utover den bestilte, så de gjelder nøyaktig i denne modusen —
    #: både for lenkene vi køer og for omdirigeringene vi følger.
    krype = maks_sider > 1

    start = time.monotonic()
    blokkert: dict[tuple[str, str], int] = {}
    funn: dict[str, dict] = {}
    sider: list[dict] = []

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
                      f"--host-resolver-rules={regler}, MAP * ~NOTFOUND"]
        browser = pw.chromium.launch(args=chrom_args)
        # SERVICE WORKERS BLOKKERES (Codex P1). En registrert service
        # worker svarer på sidens forespørsler UTENFOR sidens egen
        # route-avskjæring, og kan selv hente hva den vil — altså en vei
        # rundt egressvakten, i en motor hvis hele kontrakt er at egressen
        # er lukket. Axe trenger dem ikke: kontrollen kjører mot DOM-en
        # slik den er lastet.
        ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                                  locale="nb",
                                  service_workers="block",
                                  ignore_https_errors=tls_usikker)

        def tell(vert: str, art: str) -> None:
            # `_rapportvert` er porten mot rapportskjemaet: en blokkering
            # skal bli en dekningsbegrensning, aldri et avbrutt oppdrag.
            n = _rapportvert(vert)
            blokkert[(n, art)] = blokkert.get((n, art), 0) + 1

        def vakt(route):
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
            # ROBOTS GJELDER OGSÅ OMDIRIGERINGSMÅLET (Codex P1). Filteret
            # sto bare på lenkene VI la i køen, og et 30x er ikke en lenke:
            # en tillatt `/gaa` som svarer 301 til `Disallow: /privat/side`
            # fortsatte her fordi målet var samme origin, og axe kjørte mot
            # den forbudte siden. Etter at rapporten begynte å navngi den
            # landede URL-en, ble den til og med promotert evidens for en
            # side robots eksplisitt stengte — altså en rett vei rundt
            # porten, åpnet av målet selv.
            #
            # Bare NAVIGASJONER som kom fra en omdirigering måles: den
            # bestilte `mal_url` er kundens eget valg og gjelder som før,
            # og subressurser (bilder, stilark) er ikke sider vi crawler.
            if (krype and req.redirected_from is not None
                    and req.is_navigation_request()
                    and not _tillatt(_robotsti(u), disallow)):
                # Blokkeringen er en DEKNINGSBEGRENSNING, ikke et avbrudd:
                # siden faller ut av crawlen, og rapporten sier hvorfor.
                tell(u.hostname or u.netloc, "annet")
                route.abort("blockedbyclient")
                return
            route.continue_()

        # WEBSOCKETS AVSKJÆRES FOR SEG (Codex P1). `BrowserContext.route`
        # ser bare HTTP-forespørsler; en `new WebSocket("wss://…")` på en
        # kontrollert side gikk rett forbi vakten til en hvilken som helst
        # ekstern eller intern endepunkt. Resolverregelen over stopper
        # fremmede VERTSNAVN, men ikke en rå IP-literal — så kanalen må
        # også lukkes her, og blokkeringen TELLES som alt annet blokkert.
        def vakt_ws(ws):
            u = urllib.parse.urlsplit(ws.url)
            # `_origin` kjenner ws/wss og deres standardporter, så en
            # websocket måles mot NØYAKTIG samme origin som HTTP-vakten.
            if _origin(u) == origin:
                ws.connect_to_server()
                return
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
        oppdaget = {_normaliser_lenke(origin, mal_url, mal_url) or mal_url}
        besokt = 0
        while ko and besokt < maks_sider:
            url = ko.pop(0)
            besokt += 1
            try:
                svar = page.goto(url, wait_until="load")
                ok = svar is not None and svar.status == 200
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
            sider.append({"url": faktisk, "status": "ok" if ok else "feilet"})
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
                        sel = ", ".join(str(t) for t in node.get("target", []))
                        # Samme kutt byggeren gjør på nytt, med samme tall:
                        # selektorlengden er kontraktens grense, ikke en
                        # kapping motoren finner på bak byggerens rygg.
                        f["eksempler"].append(sel[:MAKS_SELEKTOR])
            if maks_sider > 1:
                for href in page.eval_on_selector_all(
                        "a[href]", "els => els.map(e =>"
                        " e.getAttribute('href'))"):
                    lenke = _normaliser_lenke(origin, faktisk, href or "")
                    if not lenke or lenke in oppdaget:
                        continue
                    # Robots matcher mot STI OG QUERY — se `_robotsti`.
                    d = urllib.parse.urlsplit(lenke)
                    if _tillatt(_robotsti(d), disallow):
                        oppdaget.add(lenke)
                        ko.append(lenke)
        browser.close()

    # AVKORTINGEN DEKKER BEGGE TAKENE (Codex P2). `truffet` var `bool(ko)`
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
    elif eksempler_avkortet:
        avkortet = [True, MAKS_EKSEMPLER, eksempler_avkortet]
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
