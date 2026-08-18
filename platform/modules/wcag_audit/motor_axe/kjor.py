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

  * EGRESS ER LUKKET: kun målvertens origin slipper ut. Alle andre
    forespørsler blokkeres og TELLES ({vert, antall, art}) — det er
    tallene `dekningsbegrensninger` bygges av (port 18). Ingen
    credentials: prosessen arver bare motor-allowlistens miljø.
  * MÅLET ER OFFENTLIG OG PINNET: vertsnavnet slås opp ÉN gang, hver
    adresse må være global, og både robots-henting og nettleser låses
    til den ene IP-en. Se `_pin_mal_ip`.
  * ROBOTS RESPEKTERES (port 20): `Disallow` for `User-agent: *` følges
    ved crawl; robots.txt 5xx → INGEN crawl — kun den eksplisitt
    bestilte `mal_url` kontrolleres (kunden har selv pekt på den; å
    crawle videre uten en lesbar robots er å gjette på lov).
  * TAKET ER SYNLIG (port 19): crawlen stopper på `maks_sider`, og
    `avkortet` bærer (truffet, tak, oppdagede-URL-er) — aldri en stille
    trunkering.

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


def _parse_robots(tekst: str) -> list[str]:
    """Disallow-prefiksene for `User-agent: *` — enkel, prefiksbasert
    robots-lesing (RFC 9309-kjernen; wildcards i stier støttes ikke og
    behandles da som bokstavelige prefikser, som er den STRENGE lesningen
    for oss: vi crawler mindre, aldri mer)."""
    disallow, gjelder = [], False
    for linje in tekst.splitlines():
        linje = linje.split("#", 1)[0].strip()
        if not linje or ":" not in linje:
            continue
        felt, verdi = (d.strip() for d in linje.split(":", 1))
        felt = felt.lower()
        if felt == "user-agent":
            gjelder = verdi == "*"
        elif felt == "disallow" and gjelder and verdi:
            disallow.append(verdi)
    return disallow


def _tillatt(sti: str, disallow: list[str]) -> bool:
    return not any(sti.startswith(d) for d in disallow)


def _normaliser_lenke(base_origin: str, side_url: str, href: str
                      ) -> str | None:
    """Absolutt URL på målets origin, uten fragment/query — ellers None."""
    try:
        u = urllib.parse.urljoin(side_url, href)
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return None
    if f"{p.scheme}://{p.netloc}" != base_origin:
        return None
    if p.query:
        return None
    return f"{base_origin}{p.path or '/'}"


def main() -> int:
    payload = json.loads(sys.stdin.read())
    mal_url = payload["mal_url"]
    tags = KRAVSETT_TAGS[payload["kravsett"]]
    maks_sider = int(payload["maks_sider"]) if (
        payload.get("omfang") == "nettsted") else 1

    p = urllib.parse.urlsplit(mal_url)
    origin = f"{p.scheme}://{p.netloc}"
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
    disallow, krype_lov = _robots(origin, mal_pin, tls_kontekst)
    if maks_sider > 1 and not krype_lov:
        maks_sider = 1          # robots 5xx: kun den bestilte siden

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
        ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                                  locale="nb",
                                  ignore_https_errors=tls_usikker)

        def vakt(route):
            u = urllib.parse.urlsplit(route.request.url)
            if f"{u.scheme}://{u.netloc}" == origin:
                route.continue_()
                return
            art = ART.get(route.request.resource_type, "annet")
            n = (u.hostname or u.netloc or "ukjent").lower()
            blokkert[(n, art)] = blokkert.get((n, art), 0) + 1
            route.abort("blockedbyclient")

        ctx.route("**/*", vakt)
        page = ctx.new_page()
        page.set_default_timeout(SIDEFRIST_MS)

        ko: list[str] = [mal_url]
        oppdaget = {mal_url}
        besokt = 0
        while ko and besokt < maks_sider:
            url = ko.pop(0)
            besokt += 1
            try:
                svar = page.goto(url, wait_until="load")
                ok = svar is not None and svar.status == 200
            except Exception:
                ok = False
            sider.append({"url": url, "status": "ok" if ok else "feilet"})
            if not ok:
                continue
            page.evaluate(axe_js)
            res = page.evaluate(
                "tags => axe.run(document, {runOnly:"
                " {type: 'tag', values: tags}})", tags)
            for v in res.get("violations", []):
                rid = v["id"]
                f = funn.setdefault(rid, {
                    "regel_id": rid,
                    "alvorlighet": ALVORLIGHET.get(v.get("impact"), "lav"),
                    "antall": 0, "eksempler": []})
                for node in v.get("nodes", []):
                    f["antall"] += 1
                    if len(f["eksempler"]) < 10:
                        sel = ", ".join(str(t) for t in node.get("target", []))
                        f["eksempler"].append(sel[:200])
            if maks_sider > 1:
                for href in page.eval_on_selector_all(
                        "a[href]", "els => els.map(e =>"
                        " e.getAttribute('href'))"):
                    lenke = _normaliser_lenke(origin, url, href or "")
                    if (lenke and lenke not in oppdaget
                            and _tillatt(
                                urllib.parse.urlsplit(lenke).path, disallow)):
                        oppdaget.add(lenke)
                        ko.append(lenke)
        browser.close()

    truffet = bool(ko)
    resultat = {
        "regelsett_versjon": f"axe-core-{AXE_VERSJON}",
        "varighet_ms": int((time.monotonic() - start) * 1000),
        "sider": sider,
        "funn": sorted(funn.values(), key=lambda f: f["regel_id"]),
        "blokkert": [{"vert": v, "antall": n, "art": a}
                     for (v, a), n in sorted(blokkert.items())],
        "avkortet": ([True, maks_sider, len(oppdaget)] if truffet
                     else [False, None, None]),
    }
    json.dump(resultat, sys.stdout)
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
