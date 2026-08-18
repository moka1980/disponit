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
import json
import os
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


def _robots(base: str, tls_kontekst=None) -> tuple[list[str], bool]:
    """-> (disallow-prefikser for `*`, krype_lov).

    5xx OG nettverksfeil → (.., False): ingen crawl. En robots vi ikke
    fikk LEST er ikke en robots som har sagt ja — fail-open her ville
    betydd at målets verste driftsøyeblikk er øyeblikket vi crawler mest.
    Kun et tydelig 4xx (robots finnes ikke) leses som «ingen uttalte
    begrensninger» (RFC 9309 §2.3.1.3)."""
    try:
        with urllib.request.urlopen(base + "/robots.txt", timeout=10,
                                    context=tls_kontekst) as r:
            status, tekst = r.status, r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return [], 400 <= e.code < 500
    except Exception:
        return [], False
    if status >= 500:
        return [], False
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
    robots_origin = origin
    if (p.hostname or "") in vertskart:
        ip = vertskart[p.hostname]
        port = f":{p.port}" if p.port else ""
        robots_origin = f"{p.scheme}://{ip}{port}"
    disallow, krype_lov = _robots(robots_origin, tls_kontekst)
    if maks_sider > 1 and not krype_lov:
        maks_sider = 1          # robots 5xx: kun den bestilte siden

    start = time.monotonic()
    blokkert: dict[tuple[str, str], int] = {}
    funn: dict[str, dict] = {}
    sider: list[dict] = []

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        chrom_args = ["--disable-dev-shm-usage"]
        if vertskart:
            regler = ", ".join(f"MAP {v} {ip}"
                               for v, ip in sorted(vertskart.items()))
            chrom_args.append(f"--host-resolver-rules={regler}")
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
