"""PR-011 CP1: UI-servering — CSP, sikkerhetsheadere, innholdstyper,
filallowlist/traversal, og den statiske «ingen inline / ingen innerHTML»-
porten (klarsignal V4 + V6, Codex-gate 5).

Testes mot en ISOLERT Starlette-app bygget av kun UI-rutene: servering rører
aldri databasen, så porten skal bevises uten Postgres (og uten å hoppe over
testen i CI).
"""
import json
import re
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from ui import server as uiserver


def _klient() -> TestClient:
    app = Starlette(routes=[
        Route("/", uiserver.ui_index, methods=["GET"]),
        Route("/ui/locale/{sprak}", uiserver.ui_locale, methods=["GET"]),
        Route("/ui/{sti:path}", uiserver.ui_asset, methods=["GET"]),
    ])
    return TestClient(app)


# --- CSP + sikkerhetsheadere (V4, gate 5) ----------------------------------

def test_skall_har_eksakt_ui_csp_og_sikkerhetsheadere():
    r = _klient().get("/")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    # ORDRETT den godkjente V4-strengen — ikke «inneholder default-src».
    assert r.headers["content-security-policy"] == uiserver.UI_CSP
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_ui_csp_forbyr_inline_og_eval():
    csp = uiserver.UI_CSP
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
    assert "script-src 'self'" in csp and "style-src 'self'" in csp
    assert "default-src 'none'" in csp and "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp


def test_alle_ressurser_baerer_samme_headere():
    k = _klient()
    for sti, ct in (("/ui/tokens.css", "text/css; charset=utf-8"),
                    ("/ui/css/base.css", "text/css; charset=utf-8"),
                    ("/ui/css/komponenter.css", "text/css; charset=utf-8"),
                    ("/ui/js/app.js", "text/javascript; charset=utf-8"),
                    ("/ui/js/dom.js", "text/javascript; charset=utf-8")):
        r = k.get(sti)
        assert r.status_code == 200, sti
        assert r.headers["content-type"] == ct, sti
        assert r.headers["content-security-policy"] == uiserver.UI_CSP, sti


# --- Tokenkilde + base-CSS er faktisk det vi serverer ----------------------

def test_tokens_og_base_har_forventet_innhold():
    k = _klient()
    assert ":root" in k.get("/ui/tokens.css").text
    assert "--skygge" in k.get("/ui/tokens.css").text        # CP1-tillegg
    base = k.get("/ui/css/base.css").text
    assert ".sr-only" in base and ".hoppelenke" in base


# --- Locale (locales/ er sannheten) ----------------------------------------

def test_locale_nb_og_en_serveres_ellers_404():
    k = _klient()
    nb = k.get("/ui/locale/nb")
    assert nb.status_code == 200
    assert nb.headers["content-type"] == "application/json; charset=utf-8"
    data = json.loads(nb.text)
    assert data["beslutning.TILLAT"] == "Tillatt"
    assert json.loads(k.get("/ui/locale/en").text)["beslutning.TILLAT"] \
        == "Allowed"
    assert k.get("/ui/locale/xx").status_code == 404
    assert k.get("/ui/locale/../nb").status_code == 404


# --- Filallowlist + traversal ----------------------------------------------

def test_ukjent_endelse_og_ukjent_fil_er_404():
    k = _klient()
    assert k.get("/ui/server.py").status_code == 404       # ikke i _CT
    assert k.get("/ui/finnesikke.js").status_code == 404
    assert k.get("/ui/css/finnesikke.css").status_code == 404


def test_traversal_utenfor_static_avvises():
    # Direkte mot vakten: en oppløst sti utenfor basen gir None uansett hva
    # URL-normaliseringen i klienten gjør.
    utenfor = uiserver.STATISK / ".." / ".." / "api" / "app.py"
    assert uiserver._les_trygt(uiserver.STATISK, utenfor) is None
    # tokens.css leses KUN fra design/, ikke fra static/.
    assert uiserver._les_trygt(uiserver.STATISK,
                               uiserver.STATISK / "tokens.css") is None


# --- Statisk «ingen inline / ingen innerHTML»-port (V4 + V6, gate 5) --------

_STATISK_ROT = Path(uiserver.STATISK)


def _alle(endelser):
    return [p for p in _STATISK_ROT.rglob("*") if p.suffix in endelser]


def test_ingen_inline_script_style_eller_handlers_i_html():
    html = (_STATISK_ROT / "index.html").read_text(encoding="utf-8")
    # Ingen inline <script>MED innhold</script> (ekstern src er lov).
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html), \
        "inline script i index.html"
    assert "<style" not in html.lower(), "inline <style> i index.html"
    assert not re.search(r"\son\w+\s*=", html), "inline on*-handler i html"
    assert not re.search(r"\sstyle\s*=", html), "inline style-attributt i html"


def test_ingen_innerhtml_eller_eval_i_js():
    for p in _alle({".js"}):
        kilde = p.read_text(encoding="utf-8")
        # `dom.js` NEVNER innerHTML i en vaktsjekk/kommentar; tillat ordet der
        # det ikke er en tilordning, men forby faktisk bruk.
        assert not re.search(r"\.innerHTML\s*=", kilde), f"innerHTML i {p.name}"
        assert not re.search(r"\bouterHTML\s*=", kilde), f"outerHTML i {p.name}"
        assert not re.search(r"\beval\s*\(", kilde), f"eval i {p.name}"
        assert "insertAdjacentHTML" not in kilde, f"insertAdjacentHTML {p.name}"


def test_nginx_ui_location_setter_samme_ui_csp_og_skjuler_oppstrom():
    # UI-CSP-en i nginx-malen må være ORDRETT lik appens konstant — ellers
    # kan de to drive fra hverandre uten at noe merker det. Og hver UI-
    # location må skjule oppstrøms-CSP så UI-svaret bærer nøyaktig én.
    mal = (Path(uiserver._ROT) / "deploy" / "staging" / "nginx"
           / "disponit-https.conf.template").read_text(encoding="utf-8")
    assert "location = / {" in mal and "location /ui/ {" in mal
    assert mal.count("proxy_hide_header Content-Security-Policy;") >= 2
    assert mal.count(f'"{uiserver.UI_CSP}"') >= 2, \
        "nginx UI-CSP matcher ikke uiserver.UI_CSP ordrett"
    # API-ets strenge CSP står fortsatt (server-nivå) for /v1-stiene.
    assert "default-src 'none'; frame-ancestors 'none'; base-uri 'none'" in mal


def test_ingen_hardkodet_farge_eller_avstand_i_ui_css():
    # Alt utseende via var(--token) (RUTINER pkt. 6). Komponent-/base-CSS skal
    # ikke bære egne hex-farger eller px-avstander (px kun i token-fila).
    for p in _alle({".css"}):
        if p.name == "tokens.css":
            continue
        for linje in p.read_text(encoding="utf-8").splitlines():
            s = linje.strip()
            if s.startswith("/*") or s.startswith("*") or not s:
                continue
            assert not re.search(r"#[0-9a-fA-F]{3,8}\b", s), \
                f"hardkodet hex i {p.name}: {s}"
