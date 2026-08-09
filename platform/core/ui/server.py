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

from pathlib import Path

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

# --- UI-CSP (klarsignal V4, ordrett) ---------------------------------------
UI_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
    "manifest-src 'self'; upgrade-insecure-requests"
)

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
