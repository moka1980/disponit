"""M-1 kundeflate — same-origin servering fra selve appen (PR-011).

Hvorfor appen og ikke bare nginx (klarsignal V4): CSP-en for UI-et defineres
i DENNE leveransen, og en header som skal PORTES (gate 5: «CSP-header
verifisert; inline script og handlers blokkert») må kunne måles i CI uten en
kjørende nginx. Derfor eier disse handlerne UI-ets sikkerhetsheadere direkte —
JSON-API-et beholder sin strenge `default-src 'none'` fra nginx uendret, og de
to lagene kolliderer ikke: nginx setter INGEN CSP på UI-stiene (egne
location-blokker), så appens UI-CSP er den eneste på UI-svar.

Ingenting her rører databasen: statisk servering skal ikke kunne feile fordi
en pool er tom. Rene funksjoner, ren lesing fra disk, lukket filallowlist.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from miljo import gjeldende_miljo
from starlette.requests import Request
from starlette.responses import Response

# --- Stier (lukket) --------------------------------------------------------
_UI = Path(__file__).resolve().parent
STATISK = _UI / "static"
_ROT = _UI.parents[2]                 # platform/core/ui -> repo-rot
DESIGN = _ROT / "design"
LOCALES = _ROT / "locales"

#: Tokenkilden bor i design/ (RUTINER pkt. 6) og serveres uendret under /ui/.
#: Alt annet kommer fra static/. Ingen andre kataloger er nåbare.
_EKSTRA = {"tokens.css": DESIGN / "tokens.css"}

#: Kun disse filtypene serveres — en ukjent endelse er 404, ikke et forsøk.
_CT = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}

# --- UI-CSP (klarsignal V4 + PR-011b-korreksjon) ---------------------------
# V4 spesifiserte `form-action 'self'`. Men innloggingen er en native-form-POST
# til /v1/oidc/start som 303-redirecter til IdP-en (accounts.google.com m.fl.);
# `form-action 'self'` BLOKKERER den redirecten i browseren (funnet da eier
# klikket — E2E-en brukte injisert øktcookie og traff aldri den ekte veien).
# form-action MÅ derfor tillate providerens autorisasjons-origin. Origin(ene)
# er deploy-spesifikke → env `DISPONIT_UI_IDP_ORIGINS` (mellomrom-separert),
# tom = kun 'self' (utvikling/CI). Ingen hardkodet provider i repoet.
# --- Kanonisk HTTPS-origin-parser/serializer (P1, Codex) -------------------
# `DISPONIT_UI_IDP_ORIGINS` er deploy-config, men MÅ IKKE interpoleres rått i
# en CSP-header eller nginx-konfig: et semikolon, anførselstegn, linjeskift
# eller sed-metategn kunne ellers endret sikkerhetspolicyen. Vi parser hver
# token til en KANONISK https-origin (https://host[:port]) — utdata kan per
# konstruksjon aldri inneholde CSP-/sed-metategn.

#: DNS-vertsnavn: punktseparerte labels, hver 1–63 tegn [a-z0-9-], ikke ledende/
#: etterfølgende bindestrek.
_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$", re.IGNORECASE)


def _kanon_origin(token: str) -> str | None:
    """Én token → `https://host[:port]` eller None. Avviser alt annet: annet
    skjema, userinfo, path/query/fragment, ugyldig vert eller port."""
    try:
        d = urlsplit(token)
    except ValueError:
        return None
    if d.scheme != "https" or d.username or d.password:
        return None
    if d.path or d.query or d.fragment:
        return None
    host = d.hostname or ""
    if not _HOST_RE.fullmatch(host):
        return None
    try:
        port = d.port
    except ValueError:            # ikke-numerisk/utenfor u16
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    return "https://" + host.lower() + (f":{port}" if port else "")


def kanoniske_idp_origins(raa, *, streng: bool = False) -> list[str]:
    """Mellomrom-separerte HTTPS-origins → validert, kanonisk, dedup-liste.
    Ugyldige tokens FORKASTES (fail-closed: de kan aldri utvide policyen);
    `streng=True` KASTER i stedet — brukt av deploy for fail-closed utrulling."""
    ut: list[str] = []
    sett: set[str] = set()
    for token in (raa or "").split():
        o = _kanon_origin(token)
        if o is None:
            if streng:
                raise ValueError(f"ugyldig HTTPS-origin: {token!r}")
            continue
        if o not in sett:
            sett.add(o)
            ut.append(o)
    return ut


def kanoniske_idp_origins_streng(raa: str) -> str:
    """For deploy: kanonisk, mellomrom-joinet streng. KASTER ved ugyldig
    token (fail-closed) — deploy skal avbryte, ikke rendre en svekket policy."""
    return " ".join(kanoniske_idp_origins(raa, streng=True))


def bygg_ui_csp(idp_origins: str) -> str:
    """UI-CSP med form-action = 'self' + KANONISERTE IdP-origins. Samme
    funksjon brukes av appen og av parity-testen mot nginx-malen. Rå input
    kan aldri injisere en direktiv — kun validerte origins slipper gjennom."""
    origins = kanoniske_idp_origins(idp_origins)
    form_action = "form-action 'self'" + "".join(f" {o}" for o in origins)
    return (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; font-src 'self'; object-src 'none'; "
        f"base-uri 'none'; frame-ancestors 'none'; {form_action}; "
        "manifest-src 'self'; upgrade-insecure-requests"
    )


_IDP_ORIGINS = os.environ.get("DISPONIT_UI_IDP_ORIGINS", "")
UI_CSP = bygg_ui_csp(_IDP_ORIGINS)

#: Provider-ID: samme lukkede mønster som backendens oidc_provider-check.
_PROVIDER_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

#: Speiler nginx' øvrige responsheadere (PR-009b) — settes her fordi UI-svar
#: forlater appen, og UI-location i nginx bevisst IKKE re-erklærer CSP.
_SIKKERHET = {
    "Content-Security-Policy": UI_CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def _svar(data: bytes, ct: str) -> Response:
    return Response(content=data, media_type=ct, headers=dict(_SIKKERHET))


def _les_trygt(basis: Path, kandidat: Path) -> bytes | None:
    """Les `kandidat` KUN hvis den ligger inne i `basis` og er en fil.

    `resolve()` kollapser `..`/symlenker FØR sammenligningen, så en sti som
    `/ui/../../etc/passwd` peker utenfor `basis` og avvises — vi tester at den
    oppløste stien faktisk er etterkommer av den oppløste basen, ikke bare at
    strengen ser snill ut.
    """
    try:
        full = kandidat.resolve()
        full.relative_to(basis.resolve())
    except (ValueError, OSError):
        return None
    if not full.is_file():
        return None
    try:
        return full.read_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# GET / — SPA-skallet. Hash-ruting skjer i klienten, så ÉN skall-rute holder.
# ---------------------------------------------------------------------------

def ui_index(request: Request) -> Response:
    data = _les_trygt(STATISK, STATISK / "index.html")
    if data is None:                  # byggefeil, ikke en brukervei
        return Response("Not Found", status_code=404)
    return _svar(data, _CT[".html"])


# ---------------------------------------------------------------------------
# GET /ui/{sti} — statiske ressurser (js/css/svg/woff2) + tokenkilden.
# ---------------------------------------------------------------------------

def ui_asset(request: Request) -> Response:
    sti = request.path_params.get("sti", "")
    endelse = "." + sti.rsplit(".", 1)[-1] if "." in sti else ""
    if endelse not in _CT:
        return Response("Not Found", status_code=404)
    if sti in _EKSTRA:
        data = _les_trygt(_EKSTRA[sti].parent, _EKSTRA[sti])
    else:
        data = _les_trygt(STATISK, STATISK / sti)
    if data is None:
        return Response("Not Found", status_code=404)
    return _svar(data, _CT[endelse])


# ---------------------------------------------------------------------------
# GET /ui/locale/{sprak}.json — locales/ er sannheten for all tekst.
# ---------------------------------------------------------------------------

_SPRAK = {"nb", "en"}


def ui_locale(request: Request) -> Response:
    sprak = request.path_params.get("sprak", "")
    if sprak not in _SPRAK:           # lukket allowlist, aldri fri filsti
        return Response("Not Found", status_code=404)
    data = _les_trygt(LOCALES, LOCALES / f"{sprak}.json")
    if data is None:
        return Response("Not Found", status_code=404)
    return _svar(data, _CT[".json"])


# ---------------------------------------------------------------------------
# GET /ui/oppsett.json — provider-valg for innloggingsformen (deploy-satt).
# DYNAMISK, ikke statisk fil: `DISPONIT_UI_PROVIDER` settes ved deploy, så en
# redeploy aldri resetter provideren til en test-verdi. Tom → innloggings-
# flaten viser «ikke konfigurert» i stedet for å poste en ugyldig provider.
# ---------------------------------------------------------------------------

def ui_oppsett(request: Request) -> Response:
    provider = os.environ.get("DISPONIT_UI_PROVIDER", "").strip()
    # json.dumps gjør verdien injeksjonstrygg i svaret; i tillegg fail-closed
    # mot samme lukkede mønster som backenden — ugyldig → tom (flaten sier
    # «ikke konfigurert» i stedet for å poste en umulig provider).
    if not _PROVIDER_RE.match(provider):
        provider = ""
    # `miljo` er det forsiden trenger for å avgjøre om noe kan LOVES en kunde.
    # `driftstilstand: produksjon` sier hvor koden KJØRER; det sier ingenting om
    # hvilke policystatuser verten godtar. Kjører prosessen i staging-modus,
    # binder policyer merket `utkast` beslutningene — og da er «Tilgjengelig» et
    # løfte kunden ikke kan innfri. Verdien leses fra den SAMME `DISPONIT_MILJO`
    # som `policyregister.tillatte_statuser`, så brikka og regelverket som
    # faktisk binder beslutningene ikke kan komme i utakt.
    #
    # Fail-closed som provider over: alt annet enn den eksakte strengen
    # `produksjon` er staging. Sammenligningen gjøres IKKE her, men i `miljo`,
    # som registeret bruker: leste denne flaten variabelen mildere — f.eks.
    # ved å strippe blanktegn — ville ` produksjon ` gitt «Tilgjengelig» på
    # forsiden mens registeret fortsatt sto i staging og lot `utkast` binde
    # beslutninger. Da faller de to ikke lenger sammen, som er hele poenget.
    miljo = gjeldende_miljo()
    data = json.dumps({"provider_id": provider, "miljo": miljo}).encode("utf-8")
    return _svar(data, _CT[".json"])
