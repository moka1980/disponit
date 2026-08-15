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
        Route("/ui/oppsett.json", uiserver.ui_oppsett, methods=["GET"]),
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
    # Nginx-malen bruker ${DISPONIT_UI_IDP_ORIGINS}-plassholder for form-action
    # (deploy-substituert). Med SAMME origins må rendret nginx-CSP være ORDRETT
    # lik appens bygg_ui_csp(origins) — bygget av samme funksjon, så de ikke
    # kan drive fra hverandre.
    mal = (Path(uiserver._ROT) / "deploy" / "staging" / "nginx"
           / "disponit-https.conf.template").read_text(encoding="utf-8")
    assert "location = / {" in mal and "location /ui/ {" in mal
    assert mal.count("proxy_hide_header Content-Security-Policy;") >= 2
    assert "${DISPONIT_UI_IDP_ORIGINS}" in mal, "form-action ikke plassholder-drevet"
    origins = "https://accounts.google.com"
    rendret = mal.replace("${DISPONIT_UI_IDP_ORIGINS}", origins)
    forventet = uiserver.bygg_ui_csp(origins)
    assert rendret.count(f'"{forventet}"') >= 2, \
        "rendret nginx UI-CSP matcher ikke bygg_ui_csp(origins)"
    # API-ets strenge CSP står fortsatt (server-nivå) for /v1-stiene.
    assert "default-src 'none'; frame-ancestors 'none'; base-uri 'none'" in mal


def test_form_action_er_env_drevet():
    # Uten env: kun 'self'. Med IdP-origin: den er lagt til (V4-korreksjon).
    assert uiserver.bygg_ui_csp("") .count("form-action 'self';") == 1
    csp = uiserver.bygg_ui_csp("https://accounts.google.com")
    assert "form-action 'self' https://accounts.google.com;" in csp


@pytest.mark.parametrize("ondt", [
    "https://ok.example; script-src *",          # CSP-direktiv-injeksjon
    'https://ok.example" ; add_header Evil x',   # anførsel + nginx-direktiv
    "https://ok.example\nadd_header Evil x",     # linjeskift-injeksjon
    "https://a|b",                               # sed-metategn
    "https://a&b.example",                       # sed &
    "http://ok.example",                         # feil skjema
    "javascript:alert(1)",                       # farlig skjema
    "https://user:pw@ok.example",                # userinfo
    "https://ok.example/path",                   # path
    "https://ok.example?q=1",                    # query
    "https://ok.example#frag",                   # fragment
    "https://ok.example:99999",                  # port utenfor u16
    "https://ok.example:abc",                    # ikke-numerisk port
    "https://-leading.example",                  # ugyldig label
    "'self'",                                    # CSP-nøkkelord, ikke origin
    "*",
])
def test_idp_origins_injeksjon_forkastes(ondt):
    # Fail-closed: kun rene kanoniske origins kan overleve; ingen metategn og
    # ingen fremmed direktiv slipper inn i CSP-en. (Noen input har en gyldig
    # origin FØR søppelet — den delen beholdes, injeksjonen forkastes.)
    for o in uiserver.kanoniske_idp_origins(ondt):
        # Kanonisk origin har KUN https://host[:port] — ingen av disse metategn
        # (/ og : er lovlige i selve origin-strengen).
        assert o.startswith("https://")
        assert not (set(o) & set(' ";\n\t|&*\\<>#?@')), f"metategn i {o!r}"
    csp = uiserver.bygg_ui_csp(ondt)
    for forbudt in ("script-src *", "add_header", "\n", '"', ";add", "|", "&",
                    "javascript:", "*;", "user:pw"):
        assert forbudt not in csp.replace("script-src 'self'", ""), \
            f"{forbudt!r} lekket inn i CSP fra {ondt!r}"
    # streng-modus (deploy) KASTER på ethvert ugyldig token (fail-closed).
    with pytest.raises(ValueError):
        uiserver.kanoniske_idp_origins_streng(ondt)


def test_idp_origins_gyldige_kanoniseres_og_dedupes():
    assert uiserver.kanoniske_idp_origins("https://accounts.google.com") \
        == ["https://accounts.google.com"]
    # skjema/vert lowercases; port beholdes
    assert uiserver.kanoniske_idp_origins("HTTPS://Accounts.Google.COM:443") \
        == ["https://accounts.google.com:443"]
    # flere + dedup, stabil rekkefølge
    assert uiserver.kanoniske_idp_origins(
        "https://a.example https://a.example https://b.example") \
        == ["https://a.example", "https://b.example"]
    # streng-serializer joiner med mellomrom (trygt for sed/CSP)
    assert uiserver.kanoniske_idp_origins_streng(
        "https://a.example https://b.example:8443") \
        == "https://a.example https://b.example:8443"
    # bygg_ui_csp legger dem i form-action
    csp = uiserver.bygg_ui_csp("https://accounts.google.com")
    assert "form-action 'self' https://accounts.google.com;" in csp


def test_oppsett_provider_id_fail_closed(monkeypatch):
    # Ugyldig provider (injeksjonsforsøk / rare tegn) → tom, ikke rå passthrough.
    for ondt in ('a"b', "a b", "a;b", "../x", "A".ljust(65, "A")):
        monkeypatch.setenv("DISPONIT_UI_PROVIDER", ondt)
        # Bare provider-feltet påstås her: svaret bærer også `miljo`, og en
        # helhetssammenligning ville gjort testen rød hver gang endepunktet
        # får et felt som ikke angår fail-closed på provider.
        assert json.loads(_klient().get("/ui/oppsett.json").text)["provider_id"] \
            == "", ondt


def test_oppsett_json_er_env_drevet(monkeypatch):
    # provider_id kommer fra DISPONIT_UI_PROVIDER (deploy-satt), aldri en
    # statisk fil i repoet — så en redeploy ikke resetter den.
    monkeypatch.setenv("DISPONIT_UI_PROVIDER", "google")
    r = _klient().get("/ui/oppsett.json")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    assert json.loads(r.text)["provider_id"] == "google"
    monkeypatch.delenv("DISPONIT_UI_PROVIDER", raising=False)
    assert json.loads(_klient().get("/ui/oppsett.json").text)["provider_id"] == ""
    # ingen statisk oppsett.json igjen i repoet
    assert not (uiserver.STATISK / "oppsett.json").exists()


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


def test_oppsett_oppgir_miljo_fail_closed(monkeypatch):
    """`/ui/oppsett.json` bærer miljøet forsiden avgjør løfter fra.

    Fail-closed som provider: alt annet enn den eksakte strengen `produksjon`
    blir `staging`. En skrivefeil i miljøfila skal koste et løfte, ikke gi et,
    og en tom verdi skal ikke arve produksjon fra en tidligere deploy.
    """
    k = _klient()
    for verdi, forventet in (("produksjon", "produksjon"),
                             ("staging", "staging"),
                             ("produksjonn", "staging"),
                             (" produksjon ", "produksjon"),
                             ("", "staging")):
        monkeypatch.setenv("DISPONIT_MILJO", verdi)
        assert k.get("/ui/oppsett.json").json()["miljo"] == forventet, verdi
    monkeypatch.delenv("DISPONIT_MILJO", raising=False)
    assert k.get("/ui/oppsett.json").json()["miljo"] == "staging"
