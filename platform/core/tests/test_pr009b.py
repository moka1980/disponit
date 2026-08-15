"""PR-009b: transport — nginx-konfigens kontrakter.

Driftslaget (ekte ACME, TLS-handshake, portskann) måles på disponit.com;
det som er en EGENSKAP VED KONFIGEN måles her, statisk, så en redigering
som bryter en kontrakt ikke kan gli forbi CI. Templatene rendres med et
testhostnavn og assertene kjøres mot resultatet — samme tekst nginx laster.
"""
import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
NGINX = ROT / "deploy/staging/nginx"
HOST = "test.disponit.example"


def _render(navn):
    mal = (NGINX / navn).read_text(encoding="utf-8")
    return mal.replace("${DISPONIT_HOST}", HOST)


def _https():
    return _render("disponit-https.conf.template")


def _http():
    return _render("disponit-http.conf.template")


# ---------------------------------------------------------------------------
# Tillitsgrensen: socket, aldri TCP
# ---------------------------------------------------------------------------

def test_proxy_gaar_til_unix_socket_aldri_tcp():
    """PR-009b §0: hele tillitsgrensen hviler på at API-et nås via
    Unix-socketen. En proxy_pass til 127.0.0.1:8099 ville gjenåpnet den
    loopback-veien en kompromittert M-37-arbeider kunne nådd."""
    https = _https()
    assert "proxy_pass http://unix:/run/disponit/api.sock;" in https
    assert "8099" not in https, "TCP-porten skal ikke finnes noe sted"
    assert not re.search(r"proxy_pass\s+http://127\.0\.0\.1", https)


# ---------------------------------------------------------------------------
# TLS: eksakt 1.2+1.3, moderne ciphers, ingen CBC/RSA-kx
# ---------------------------------------------------------------------------

def test_tls_protokoller_og_ciphers_er_eksakte():
    https = _https()
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in https
    assert "TLSv1.1" not in https and "TLSv1.3;" in https
    assert "SSLv3" not in https
    # Kun ECDHE-GCM/CHACHA — ingen CBC (blokkchiffer), ingen ren RSA-kx.
    ciphers = re.search(r"ssl_ciphers ([^;]+);", https).group(1)
    assert "CBC" not in ciphers
    assert not re.search(r"(^|:)AES\d+-", ciphers), "ren RSA-key-exchange"
    for krav in ("ECDHE-ECDSA-AES128-GCM-SHA256", "CHACHA20-POLY1305"):
        assert krav in ciphers
    assert "ssl_conf_command Ciphersuites" in https
    assert "ssl_prefer_server_ciphers off;" in https


# ---------------------------------------------------------------------------
# Host/SNI-kontrakten: ukjent SNI avvist, ukjent Host 421, kanonisk host
# ---------------------------------------------------------------------------

def test_ukjent_sni_avvises_i_handshake():
    https = _https()
    # default_server på 443 med ssl_reject_handshake — ingen HTTP serveres.
    blokk = re.search(r"listen 443 ssl default_server;.*?\}", https, re.S)
    assert blokk and "ssl_reject_handshake on;" in blokk.group(0)


def test_host_mismatch_gir_421_uten_upstream():
    https = _https()
    assert f"if ($host != {HOST})" in https
    # 421-grenen skal returnere FØR noen proxy_pass i den blokken.
    server = re.search(r"server_name %s;.*" % re.escape(HOST), https, re.S)
    tekst = server.group(0)
    i421 = tekst.index("return 421;")
    ipass = tekst.index("proxy_pass")
    assert i421 < ipass, "Host-sjekken må gate før upstream"


def test_kanonisk_host_settes_klientverdi_erstattes():
    """v2 §1: API-et får X-Disponit-Host = konfigverdien. proxy_set_header
    ERSTATTER enhver klientsendt verdi — den kanoniske vinner alltid."""
    https = _https()
    assert f"proxy_set_header X-Disponit-Host {HOST};" in https


def test_port80_redirect_bygges_fra_kanonisk_ikke_host_header():
    """v2 §1: redirect fra $host ville vært redirect-injeksjon. Den skal
    bygges fra konfigverdien; default-serveren avviser ukjent Host med 421,
    ikke en redirect."""
    http = _http()
    assert f"return 301 https://{HOST}$request_uri;" in http
    assert "return 301 https://$host" not in http
    default = re.search(r"listen 80 default_server;.*?\}\n\}", http, re.S)
    assert default and "return 421;" in default.group(0)
    assert "301" not in default.group(0), "default skal aldri redirecte"


# ---------------------------------------------------------------------------
# Header-hygiene (v2 §2): hardkodet proto, klientvarianter nulles
# ---------------------------------------------------------------------------

def _uten_kommentarer(tekst):
    return "\n".join(re.sub(r"#.*$", "", l) for l in tekst.splitlines())


def test_forwarded_proto_hardkodet_klientvarianter_nulles():
    https = _https()
    assert "proxy_set_header X-Forwarded-Proto https;" in https
    assert "$scheme" not in _uten_kommentarer(https), \
        "proto skal være hardkodet, ikke $scheme"
    # Klientsendte infrastruktur-headere nulles eksplisitt.
    for h in ("X-Real-IP", "Forwarded"):
        assert re.search(rf'proxy_set_header {re.escape(h)} "";', https), h
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in https


def test_hop_by_hop_og_ingen_websocket():
    https = _https()
    assert "proxy_http_version 1.1;" in https
    assert 'proxy_set_header Connection "";' in https
    assert 'proxy_set_header Upgrade "";' in https
    # Ingen websocket-oppgradering i denne leveransen (v2 §4).
    assert "Upgrade $http_upgrade" not in https


def test_responsheadere_og_ingen_hsts_i_forste_deploy():
    https = _https()
    for h in ("Referrer-Policy no-referrer",
              "X-Content-Type-Options nosniff",
              "X-Frame-Options DENY",
              "Content-Security-Policy"):
        assert h in https, h
    # HSTS er BEVISST utelatt i første deploy (v2 §6) — egen auditert port.
    assert "Strict-Transport-Security" not in https, \
        "HSTS skal IKKE være i første deploy"
    assert "server_tokens off;" in https


# ---------------------------------------------------------------------------
# Callback-redaksjon (v2 §5) + rate-soner (klarsignal V3)
# ---------------------------------------------------------------------------

def test_callback_loggformat_forbyr_query_og_hemmeligheter():
    rate = (NGINX / "rate-soner.conf").read_text(encoding="utf-8")
    fmt = re.search(r"log_format disponit_callback (.*?);", rate, re.S).group(1)
    # `$request` med negativt lookahead: `$request_method` (kun metode, ingen
    # query) er TILLATT, men bare `$request`/`$request_uri` (som bærer query)
    # er forbudt.
    assert not re.search(r"\$request(?![_a-z])", fmt), "callback-loggen har $request"
    assert not re.search(r"\$request_uri", fmt)
    for forbudt in ("$args", "$http_cookie", "$http_authorization",
                    "$http_referer"):
        assert forbudt not in fmt, f"callback-loggen lekker {forbudt}"
    assert "$uri" in fmt, "callback-loggen skal ha normalisert sti"
    # Callback-lokasjonen bruker DET formatet + eget error-log-nivå.
    https = _https()
    cb = re.search(r"location = /v1/oidc/callback \{.*?\}", https, re.S).group(0)
    assert "access_log /var/log/nginx/disponit-callback.log disponit_callback;" in cb
    assert "error_log" in cb and "crit" in cb


def test_hovedloggen_har_heller_ikke_query():
    rate = (NGINX / "rate-soner.conf").read_text(encoding="utf-8")
    fmt = re.search(r"log_format disponit_main (.*?);", rate, re.S).group(1)
    assert not re.search(r"\$request_uri", fmt)
    for forbudt in ("$args", "$http_cookie"):
        assert forbudt not in fmt


def test_oidc_soner_erstatter_den_generelle_ikke_stables():
    """Klarsignal V3: OIDC-rutene bruker KUN sin egen sone. Hvis den
    generelle OGSÅ gjaldt dem, ville den strammere blitt meningsløs."""
    https = _https()
    for rute in ("location = /v1/oidc/callback", "location = /v1/oidc/start"):
        blokk = re.search(re.escape(rute) + r" \{.*?\}", https, re.S).group(0)
        assert "zone=disponit_oidc" in blokk
        assert "zone=disponit_general" not in blokk, \
            f"{rute} skal ikke ha den generelle sonen"
    generell = re.search(r"location / \{.*?\}", https, re.S).group(0)
    assert "zone=disponit_general burst=100" in generell
    rate = (NGINX / "rate-soner.conf").read_text(encoding="utf-8")
    assert "rate=600r/m" in rate and "rate=120r/m" in rate


# ---------------------------------------------------------------------------
# Kroppsgrensen: proxyen må ikke være strammere enn appen på store ruter
# ---------------------------------------------------------------------------

_ENHET = {"": 1, "k": 1024, "m": 1024 * 1024}


def test_nginx_artefaktrute_slipper_gjennom_appens_kroppsgrense():
    """PR-014b P1: appen fikk en egen, større kroppsgrense for /v1/artefakt,
    men proxyen beholdt sin server-vide `client_max_body_size 256k`. Nginx
    svarte da 413 for enhver wire-kropp over 256 KiB FØR appen så den — altså
    var 1 MiB-artefaktet ruten finnes for uoppnåelig i den ENESTE stien en
    kontroller har. Testen binder nginx-verdien til appkonstanten så de to
    tallene ikke kan gli fra hverandre igjen."""
    from api.app import MAKS_ARTEFAKT_KROPP, MAKS_KROPP, STORE_KROPP_RUTER
    https = _https()
    # Den server-vide grensen er fortsatt den STRAMME; unntaket er per rute.
    assert re.search(r"^\s*client_max_body_size 256k;", https, re.M)
    assert MAKS_KROPP == 256 * 1024

    for rute in STORE_KROPP_RUTER:
        blokk = re.search(r"location = %s \{(.*?)\n    \}" % re.escape(rute),
                          https, re.S)
        assert blokk, f"ingen egen nginx-location for {rute}"
        krop = blokk.group(1)
        m = re.search(r"client_max_body_size\s+(\d+)([kKmM]?);", krop)
        assert m, f"{rute} mangler egen client_max_body_size"
        grense = int(m.group(1)) * _ENHET[m.group(2).lower()]
        assert grense >= MAKS_ARTEFAKT_KROPP, (
            f"nginx slipper {grense} B på {rute}, appen tillater "
            f"{MAKS_ARTEFAKT_KROPP} B — proxyen avviser med 413 først")
        # Ruten mister ikke rate-grense eller socket-tillitsgrensen.
        assert "zone=disponit_general" in krop
        assert "proxy_pass http://unix:/run/disponit/api.sock;" in krop


# ---------------------------------------------------------------------------
# ACME-tilstandsmaskinen: rekkefølge og idempotens (v2 §3)
# ---------------------------------------------------------------------------

def test_acme_sekvens_henter_cert_for_https_konfig():
    raa = (ROT / "deploy/staging/opp-transport.sh").read_text(encoding="utf-8")
    # Strip kommentarlinjer — rekkefølgen skal måles på KODEN, ikke på
    # header-kommentarens beskrivelse av den.
    opp = "\n".join(l for l in raa.splitlines() if not l.lstrip().startswith("#"))
    i_http = opp.index("disponit-http.conf.template")
    i_cert = opp.index("certbot certonly")
    i_https = opp.index("disponit-https.conf.template")
    i_probe = opp.index("EKSTERN HTTPS-probe".lower()) if False else \
        opp.index("curl -s -o /dev/null")
    assert i_http < i_cert < i_https < i_probe, \
        "HTTP-konfig → cert → HTTPS-konfig → probe (v2 §3)"
    # Idempotent: ingen ny utstedelse hvis sertifikatet finnes.
    assert 'if [ ! -d "/etc/letsencrypt/live/$HOST" ]' in opp
    # nginx -t gater hver reload.
    assert opp.count("nginx -t") >= 2
    # Fornyelses-hook validerer FØR reload.
    hook = (ROT / "deploy/staging/nginx-fornyelse-hook.sh").read_text(
        encoding="utf-8")
    i_t = hook.index("nginx -t")
    i_reload = hook.index("systemctl reload")
    assert i_t < i_reload


def test_p1_helseprobe_krever_eksakt_200_ikke_bare_ikke_000():
    """P1 review-runde 1: proben avviste bare `000`, så 421/500/502/503 ble
    godkjent som «transport oppe». Verdikten skal kreve EKSAKT forventet
    status. Matrisen kjøres mot den rene funksjonen i lib-opp.sh."""
    import subprocess
    lib = ROT / "deploy/staging/lib-opp.sh"

    def verdikt(kode, forventet="200"):
        r = subprocess.run(
            ["bash", "-c",
             f'. {lib}; vurder_helsekode "{kode}" "{forventet}"'],
            capture_output=True, text=True)
        return r.returncode

    assert verdikt("200") == 0, "200 skal godkjennes"
    # Nøyaktig feilene proben finnes for — ALLE skal avvises:
    for feil in ("000", "421", "500", "502", "503", "404", "301", ""):
        assert verdikt(feil) != 0, f"{feil!r} skal avvises, ikke godkjennes"
    # Og at opp-transport faktisk BRUKER verdikten mot 200, ikke != 000.
    opp = (ROT / "deploy/staging/opp-transport.sh").read_text(encoding="utf-8")
    assert "vurder_helsekode" in opp
    assert 'if [ "$KODE" = 000 ]' not in opp, "den gamle svake sjekken er borte"


def test_nginx_bruker_i_proxy_gruppe_m37_aldri():
    """V2 (ACL-porten forberedt): opp-transport melder nginx-brukeren inn i
    disponit-proxy; ingenting her rører M-37 (den skal ha EACCES)."""
    opp = (ROT / "deploy/staging/opp-transport.sh").read_text(encoding="utf-8")
    assert "usermod -aG disponit-proxy" in opp
    assert "disponit-m37" not in opp, \
        "transport skal aldri gi M-37 socket-tilgang"
