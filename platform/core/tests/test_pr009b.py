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


def test_nginx_kandidatrutene_slipper_gjennom_appens_kroppsgrenser():
    """#173 (Cursor P1-1): tredje gang samme klasse er målt — appen ga
    kandidatrutene egne, større kroppsgrenser, ingressen sto igjen på
    256 KiB.

    En reell CV er base64 langt over 256 KiB, så nginx hadde svart 413 før
    appens rutegrense ble konsultert. Og et 413 HER er ikke en avvist
    forespørsel: sinkene i `kjor_en` reiser ikke-2xx som
    `kandidatlagring_feilet`, og `kjor_bunt` feller hele evalueringen —
    grensen felte den eneste kjøringen rutene finnes for. `TestClient`
    treffer aldri nginx, så ingen apptest ser dette.

    Porten itererer `RUTEKROPPSGRENSER`, ikke en liste her: en ny rute med
    eget apptak, men uten egen nginx-location, feller testen.

    MUTASJONEN SOM DREPER DENNE: fjern én av de to `location =`-blokkene i
    malen, eller sett `client_max_body_size` der under appkonstanten."""
    from api.app import RUTEKROPPSGRENSER
    https = _https()
    assert RUTEKROPPSGRENSER, "ingen ruter med eget kroppstak å binde"
    for rute, apptak in RUTEKROPPSGRENSER.items():
        blokk = re.search(r"location = %s \{(.*?)\n    \}" % re.escape(rute),
                          https, re.S)
        assert blokk, f"ingen egen nginx-location for {rute}"
        krop = blokk.group(1)
        m = re.search(r"client_max_body_size\s+(\d+)([kKmM]?);", krop)
        assert m, f"{rute} mangler egen client_max_body_size"
        grense = int(m.group(1)) * _ENHET[m.group(2).lower()]
        assert grense >= apptak, (
            f"nginx slipper {grense} B på {rute}, appen tillater {apptak} B"
            " — proxyen avviser med 413 først, og sinken leser det som"
            " kandidatlagring_feilet")
        # Ruten mister ikke rate-grense eller socket-tillitsgrensen.
        # HVILKEN sone måles av testen under — her er kravet bare at
        # ruten fortsatt HAR en (en location uten `limit_req` faller ut
        # av rate-vernet helt, og det er en annen og verre feil).
        assert re.search(r"limit_req zone=\w+ burst=\d+", krop), \
            f"{rute} mistet rate-grensen"
        assert "proxy_pass http://unix:/run/disponit/api.sock;" in krop


def test_nginx_kandidatrutene_faar_sin_egen_ratesone():
    """#173 (Codex P1): ingressens rate-budsjett må matche strømmen.

    Kroppsgrensen ble hevet i `56fe289`, men begge kandidatrutene sto
    igjen i `disponit_general` — 600 r/m med burst 100, altså 10 r/s.
    Skriveveien er en STRØM: inntil 25 000 skrivinger under ett claim
    (§4: 20 000 medlemmer + 5 000 kandidater). En arbeider som leverer
    små dokumenter fortere enn 10/s tømmer bursten og får nginx' 429
    lenge før appens egen `KANDIDATDATA_RATE_PER_MIN`-bøtte er i
    nærheten — altså var appfiksen i `bc1e7f3` uten virkning i den
    eneste stien som finnes i drift.

    Og et 429 her er ikke en bremset forespørsel, like lite som 413-en
    var en avvist: `lever` leser 4xx som TERMINALT, og `kjor_bunt`
    feller hele evalueringen som `kandidatlagring_feilet`. Ingressen
    felte den eneste kjøringen rutene finnes for.

    Porten er den samme formen som kroppsgrensen har: ingressen skal
    ikke være strammere enn budsjettet appen SELV håndhever, og
    appkonstanten er ankeret — ikke et tall kopiert inn i testen.

    MUTASJONEN SOM DREPER DENNE: sett rutene tilbake til
    `zone=disponit_general`, eller senk sonens rate under appbøtta."""
    from api.app import (KANDIDATARTEFAKT_RUTE, KANDIDATDATA_RATE_PER_MIN,
                         KANDIDATDOK_RUTE)
    https = _https()
    soner = (NGINX / "rate-soner.conf").read_text(encoding="utf-8")
    m = re.search(r"zone=disponit_kandidatdata:\d+[km]\s+rate=(\d+)r/m;",
                  soner)
    assert m, "ingen egen rate-sone for kandidatskriveveien"
    sonerate = int(m.group(1))
    assert sonerate >= KANDIDATDATA_RATE_PER_MIN, (
        f"ingressen slipper {sonerate}/min, appen budsjetterer"
        f" {KANDIDATDATA_RATE_PER_MIN}/min — nginx svarer 429 først, og"
        " sinken leser det som kandidatlagring_feilet")
    # Den generelle sonen er URØRT: fiksen er en egen sone for to ruter,
    # ikke en oppmyking av transportvernet for hele flaten.
    assert "zone=disponit_general:10m rate=600r/m;" in soner, \
        "den generelle sonen ble hevet i stedet for å få en søster"
    for rute in (KANDIDATDOK_RUTE, KANDIDATARTEFAKT_RUTE):
        blokk = re.search(r"location = %s \{(.*?)\n    \}" % re.escape(rute),
                          https, re.S)
        assert blokk, f"ingen egen nginx-location for {rute}"
        krop = blokk.group(1)
        assert "zone=disponit_kandidatdata" in krop, \
            f"{rute} står fortsatt i den generelle sonen"
        assert "zone=disponit_general" not in krop, \
            f"{rute} stabler den generelle sonen oppå den egne"
        assert "limit_req_status 429;" in krop, rute


def test_nginx_kandidatrutene_faar_frist_med_margin():
    """#173 (Codex P2): ingressens FRIST må ha margin over appens eget
    behandlingsbudsjett.

    Sonen og kroppsgrensen ble bundet til appkonstantene i tidligere
    runder, men begge rutene arvet fortsatt nginx' default
    `proxy_read_timeout` på 60 s — nøyaktig samme tall som
    `controller.SKRIV_BEHANDLING_S`, altså det plattformen SELV setter
    av til å behandle kroppen etter at den er mottatt. Marginen var
    null.

    Og vinduet er ekte taust: handleren parser inntil 301 MiB
    wire-JSON, kanoniserer og hasher payloaden og fullfører
    databasetransaksjonen uten å sende en byte underveis. En GYLDIG
    forespørsel som bruker budsjettet sitt ble derfor kuttet av
    ingressen — 504 — før controllerens egen `SKRIVEFRIST_S` var i
    nærheten. Tredje gang samme følge: `lever` leser ikke-2xx som
    TERMINALT, og `kjor_bunt` feller hele evalueringen som
    `kandidatlagring_feilet`.

    Ankeret er appkonstanten, ikke et tall kopiert inn i testen — samme
    form som rate-sonen og kroppsgrensen. Og det er HELE ankeret malen
    forplikter seg på: 3 × `SKRIV_BEHANDLING_S`. En port som bare krevde
    `frist > SKRIV_BEHANDLING_S` slapp `61s` gjennom CI — praktisk null
    margin under last, med samme 504 til følge.

    MUTASJONEN SOM DREPER DENNE: fjern `proxy_read_timeout` fra en av
    rutene (da gjelder defaulten på 60 s igjen), eller sett den under
    3 × `SKRIV_BEHANDLING_S` — `61s` like fullt som `60s`."""
    from api.app import KANDIDATARTEFAKT_RUTE, KANDIDATDOK_RUTE
    from modules.m57_ats.controller import SKRIVEFRIST_S, SKRIV_BEHANDLING_S
    https = _https()
    for rute in (KANDIDATDOK_RUTE, KANDIDATARTEFAKT_RUTE):
        blokk = re.search(r"location = %s \{(.*?)\n    \}" % re.escape(rute),
                          https, re.S)
        assert blokk, f"ingen egen nginx-location for {rute}"
        # Kommentarene strippes FØR matchen (Codex P2): et deaktivert
        # `# proxy_read_timeout 180s;` er nøyaktig den mutasjonen porten
        # finnes for — nginx faller da tilbake på defaulten på 60 s —
        # men en rå regex på malteksten leser den som et satt direktiv
        # og lar regresjonen passere grønt. Samme helper som
        # `$scheme`-porten over bruker.
        m = re.search(r"proxy_read_timeout\s+(\d+)s;",
                      _uten_kommentarer(blokk.group(1)))
        assert m, (
            f"{rute} arver nginx' default proxy_read_timeout (60 s) —"
            " samme tall som appens eget behandlingsbudsjett, altså null"
            " margin")
        frist = int(m.group(1))
        assert frist >= 3 * SKRIV_BEHANDLING_S, (
            f"{rute}: ingressen kutter etter {frist} s, appen budsjetterer"
            f" {SKRIV_BEHANDLING_S} s behandling — margin under 3 ×"
            " budsjettet er ingen margin under last: proxyen feller et"
            " gyldig skriv, og sinken leser 504 som kandidatlagring_feilet")
        # Og ikke lenger enn klientens egen tålmodighet: da ville nginx
        # holdt en forbindelse ingen venter på lenger.
        assert frist <= SKRIVEFRIST_S, (
            f"{rute}: ingressens frist ({frist} s) er lengre enn"
            f" controllerens SKRIVEFRIST_S ({SKRIVEFRIST_S} s)")


def test_173_kandidatrutenes_kroppstak_daekker_arkivets_maksdokument():
    """#173 (Cursor P2-5): de to takene var dokumenterte Codex-fikser
    uten port. En mutasjon tilbake til `MAKS_KROPP` slapp gjennom CI, og
    følgen er ikke en avvist forespørsel: 4xx på skriveveien leses av
    `lever` som terminalt, `kjor_bunt` feller hele evalueringen med
    `kandidatlagring_feilet`.

    Ankeret er §4s eget tall (25 MiB per dokument), ikke appens
    sammensatte uttrykk — porten er en NEDRE grense på wire-budsjettet,
    ikke en kopi av formelen.

    MUTASJONEN SOM DREPER DENNE: la en av rutene falle ut av
    `RUTEKROPPSGRENSER` (og dermed ned på `MAKS_KROPP`)."""
    from api.app import (KANDIDATARTEFAKT_RUTE, KANDIDATDOK_RUTE,
                         MAKS_KROPP, RUTEKROPPSGRENSER)
    for rute in (KANDIDATDOK_RUTE, KANDIDATARTEFAKT_RUTE):
        assert rute in RUTEKROPPSGRENSER, \
            f"{rute} faller ned på MAKS_KROPP — 413 på hver reell CV"
        assert RUTEKROPPSGRENSER[rute] > MAKS_KROPP, rute
    # §4s maksdokument, base64-kodet: den delen av dokumentkroppen som
    # ikke kan komprimeres bort. Taket må minst dekke den.
    dok_maks = 25 * 1024 * 1024
    assert RUTEKROPPSGRENSER[KANDIDATDOK_RUTE] >= (dok_maks + 2) // 3 * 4, (
        "dokumentruten rommer ikke §4s 25 MiB som base64")
    # Artefaktkroppen bærer kildeteksten selv, ikke en koding av den —
    # men på wire kan hvert tegn stå som `\\uXXXX`. Taket må dekke minst
    # den ene teksten i verste fall.
    assert RUTEKROPPSGRENSER[KANDIDATARTEFAKT_RUTE] >= 6 * dok_maks, (
        "artefaktruten rommer ikke §4-teksten i verste JSON-form")


def test_173_kandidatskrivingen_har_egen_ratebotte():
    """#173 (Cursor P2-5): bøtta var en dokumentert Codex-fiks uten port.

    En bunt kan lovlig bære 20 000 filer og 5 000 kandidater — 25 000
    skrivinger — mens standardbudsjettet er 12 000 per rullende minutt.
    Delte skrivingen den bøtta, felte plattformens egen grense den ENESTE
    kjøringen ruten finnes for.

    Porten måler begge halvdelene, for taket alene er ikke fiksen:
    nøkkelen må være EGEN, ellers sulter skrivesløyfa modulens
    claim/forny/kvittering eller sultes av dem.

    MUTASJONEN SOM DREPER DENNE: bytt tilbake til
    `slipp_gjennom(auth.token_id)` i `_kandidatdata`, eller senk taket
    under buntens dokumenterte maksima."""
    import inspect

    from api.app import KANDIDATDATA_RATE_PER_MIN, _kandidatdata
    assert KANDIDATDATA_RATE_PER_MIN >= 20_000 + 5_000, (
        f"{KANDIDATDATA_RATE_PER_MIN}/min dekker ikke buntens 25 000"
        " skrivinger — grensen feller kjøringen, ikke misbruk")
    kilde = inspect.getsource(_kandidatdata)
    assert 'slipp_gjennom("kandidatdata:"' in kilde, \
        "skrivingen deler nøkkel med modultokenets standardbøtte igjen"
    assert "tak=KANDIDATDATA_RATE_PER_MIN" in kilde, \
        "skrivingen bruker ikke sitt eget tak"


def test_173_ratebudsjettet_dekker_hele_retrykjeden():
    """#173 (Codex P2): faktoren var 2, men `lever` gjør FIRE forsøk.

    Bøtta belastes av hvert forsøk som NÅR handleren — rate-porten står
    foran databasearbeidet, så et forsøk som ender i 5xx har allerede
    tatt sin plass i vinduet. Med faktor 2 budsjetteres bare ETT retry
    per logisk skriving: en kjøring med i snitt to forbigående feil
    bruker tre forespørsler per skriving og passerer 50 000 rundt logisk
    skriving nummer 16 667, godt under kontraktens 25 000. Neste forsøk
    får en TERMINAL 429, og `lever` leser 4xx som endelig — en fullt
    gjenopprettelig evaluering felt av plattformens egen grense.

    Speilet bindes til modulens EGET tall, ikke til literalen 4: api/
    importerer aldri modulkode, så konstanten er en kopi, og en kopi som
    ingen måler er en kopi som driver. Skrus `LEVERINGSFORSOK` opp uten
    at budsjettet følger etter, er funnet tilbake — da skal denne
    testen falle, ikke en bunt i produksjon.

    MUTASJONEN SOM DREPER DENNE: sett faktoren tilbake til 2, eller la
    speilet stå igjen når modulens `LEVERINGSFORSOK` endres.
    """
    from api.app import KANDIDAT_LEVERINGSFORSOK, KANDIDATDATA_RATE_PER_MIN
    from modules.m57_ats import controller

    assert KANDIDAT_LEVERINGSFORSOK == controller.LEVERINGSFORSOK, (
        f"appens speil er {KANDIDAT_LEVERINGSFORSOK}, modulen gjør"
        f" {controller.LEVERINGSFORSOK} forsøk — kopien har drevet")
    assert KANDIDATDATA_RATE_PER_MIN >= \
        controller.LEVERINGSFORSOK * (20_000 + 5_000), (
            f"{KANDIDATDATA_RATE_PER_MIN}/min dekker ikke"
            f" {controller.LEVERINGSFORSOK} forsøk × 25 000 skrivinger —"
            " en retrykjede innenfor kontrakten treffer taket")

    # Og ingressen må følge appen, ellers svarer nginx 429 først —
    # samme binding som `test_nginx_kandidatrutene_faar_sin_egen_ratesone`
    # måler, her sett fra retrykjedens side.
    soner = (NGINX / "rate-soner.conf").read_text(encoding="utf-8")
    m = re.search(r"zone=disponit_kandidatdata:\d+[km]\s+rate=(\d+)r/m;",
                  soner)
    assert m and int(m.group(1)) >= KANDIDATDATA_RATE_PER_MIN, \
        "ingressen ble ikke hevet sammen med appbudsjettet"


def test_nginx_inndataruten_slipper_bunten_gjennom_og_redigerer_jtien():
    """#162, to Codex-funn i samme location.

    P1: opplastingsruten annonserer og reserverer `INNDATA_MAKS_FYSISK`
    (64 MiB), men traff den server-vide `client_max_body_size 256k` — nginx
    svarte 413 på hver bunt over 256 KiB, altså på alle bunter ruten finnes
    for, før appens strømmetelling så en byte.

    P2: `disponit_main` logger `$uri`, og på DENNE ruten er `$uri`
    reservasjonens engangs-jti. Loggen ville båret hver utestående
    reservasjon i klartekst. Ruten må derfor bruke et log_format som ikke
    inneholder `$uri`/`$request`/`$request_uri`."""
    from api.app import INNDATA_MAKS_FYSISK, STROEM_RUTE_PREFIKS
    https = _https()
    blokk = re.search(r"location %s \{(.*?)\n    \}"
                      % re.escape(STROEM_RUTE_PREFIKS), https, re.S)
    assert blokk, f"ingen egen nginx-location for {STROEM_RUTE_PREFIKS}"
    krop = blokk.group(1)
    m = re.search(r"client_max_body_size\s+(\d+)([kKmM]?);", krop)
    assert m, "opplastingsruten mangler egen client_max_body_size"
    grense = int(m.group(1)) * _ENHET[m.group(2).lower()]
    assert grense >= INNDATA_MAKS_FYSISK, (
        f"nginx slipper {grense} B på {STROEM_RUTE_PREFIKS}, appen tillater "
        f"{INNDATA_MAKS_FYSISK} B — proxyen avviser med 413 først")
    assert "zone=disponit_general" in krop
    assert "proxy_pass http://unix:/run/disponit/api.sock;" in krop

    # Runde 5: uten dette bufrer nginx hele kroppen FØR upstream ser den,
    # og «auth først» i appen verner da bare appen — ingressen tar imot 64
    # MiB fra en uautentisert klient uansett.
    assert re.search(r"^\s*proxy_request_buffering\s+off;", krop, re.M), \
        "opplastingsruten bufrer kroppen før appen får svare 401"

    # Redaksjonen: ruten overstyrer access_log, og formatet den peker på
    # bærer ikke stien. Begge ledd måles — et eget format som likevel
    # inneholder $uri ville vært redaksjon i navnet alene.
    m = re.search(r"^\s*access_log\s+\S+\s+(\w+);", krop, re.M)
    assert m, "opplastingsruten arver disponit_main og logger jti-en"
    navn = m.group(1)
    assert navn != "disponit_main"
    soner = (NGINX / "rate-soner.conf").read_text(encoding="utf-8")
    fmt = re.search(r"log_format %s (.*?);\n" % re.escape(navn), soner, re.S)
    assert fmt, f"log_format {navn} finnes ikke i rate-soner.conf"
    # `\b` og ikke `in`: `$request_method` er lovlig og inneholder `$request`
    # som ren delstreng — en naiv sjekk ville forbudt metoden i stedet for
    # stien.
    for variabel in ("uri", "request_uri", "args", "request"):
        assert not re.search(r"\$%s\b" % variabel, fmt.group(1)), (
            f"{navn} inneholder ${variabel} — jti-en havner i loggen likevel")

    # Runde 5: FEIL-loggen bærer den samme stien. nginx skriver sin egen
    # 413-linje på `error`-nivå med hele request-linja, så en arvet
    # `error_log ... warn` gir jti-en bort selv om access-formatet er
    # redigert — og forespørselen som utløser den ble AVVIST, altså er
    # reservasjonen fortsatt ubrukt. `crit` ligger over `error`.
    m = re.search(r"^\s*error_log\s+\S+\s+(\w+);", krop, re.M)
    assert m, "opplastingsruten arver serverens error_log og logger jti-en"
    assert m.group(1) == "crit", (
        f"error_log-nivået er {m.group(1)}; 413-linja med jti-en skrives "
        f"på error-nivå og må ligge under terskelen")


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


def test_rollbackdommen_maales_mot_bootportens_fasit_127():
    """Issue #127 (målt 2026-08-21): opp.sh meldte «forrige kode er
    fortsatt kompatibel» i samme deploy som oppsett-postgresql.sh hadde
    migrert 52+53 — «ingen nye migrasjoner i DENNE kjøringen» er et
    utsagn om kjøringen, ikke om rullbakken, og bootporten nektet
    788bd83 mot basen på 1→53 (fase 8 målte activating → failed).

    Dommen felles nå mot bootportens egen fasit: forrige releases
    migrasjonsversjoner mot basens anvendte. Matrisen kjøres mot den
    rene funksjonen; katalog-/base-lesningen prøves mot et fabrikert
    tre, og umålt er aldri kompatibelt."""
    import subprocess
    import tempfile
    from pathlib import Path as P
    lib = ROT / "deploy/staging/lib-opp.sh"

    def ren(forrige, base):
        r = subprocess.run(
            ["bash", "-c",
             f'. {lib}; vurder_rollbackmaal "{forrige}" "{base}"'],
            capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    kode, _ = ren("001 002 003", "001 002 003")
    assert kode == 0, "identiske sett skal godkjennes"
    for forrige, base in (("001 002", "001 002 003"),   # basen foran
                          ("001 002 003", "001 002"),   # forrige foran
                          ("", "001"), ("001", "")):
        kode, tekst = ren(forrige, base)
        assert kode != 0, f"({forrige!r}, {base!r}) skal avvises"
        if forrige or base:
            assert "forventer" in tekst
    # Kataloglesningen bruker bootportens nøkkel (tresifret prefiks) og
    # ignorerer alt annet i katalogen. Målt gjennom DEN FUNKSJONEN SOM
    # KJØRER i deploy — en kopi av rørledningen her ville målt kopien
    # (slik en kopi skjulte at uttrekket mistet tilbakereferansen `\1`).
    # Basen er utilgjengelig med vilje: da rapporterer funksjonen begge
    # settene den faktisk leste, og «umålt er ikke kompatibelt».
    import os
    with tempfile.TemporaryDirectory() as tmp:
        kat = P(tmp) / "platform/core/db/migrations"
        kat.mkdir(parents=True)
        for navn in ("001_init.sql", "002_x.sql", "README.md",
                     "ikke_migrasjon.sql"):
            (kat / navn).write_text("-- t", encoding="utf-8")
        r = subprocess.run(
            ["bash", "-c",
             f'. {lib}; rollbackmaal_kompatibelt "{tmp}" '
             '"postgresql:///finnes_ikke?host=/finnes-ikke-katalog"'],
            capture_output=True, text=True,
            env=dict(os.environ, PGCONNECT_TIMEOUT="1"))
        assert r.returncode != 0, "uleselig base skal aldri godkjennes"
        assert "forrige: '001 002'" in r.stdout, \
            f"kataloguttrekket skal gi bootportens nøkler: {r.stdout!r}"
        assert "umålt er ikke kompatibelt" in r.stdout
    # …og opp.sh BRUKER den målte dommen — den gamle slutningen fra
    # NYE_MIGRASJONER alene er borte.
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    assert "rollbackmaal_kompatibelt" in opp
    assert "er fortsatt kompatibel med skjemaet" not in opp
    assert "UMÅLT, ikke lovet" in opp


def test_rollbackmaalet_leses_med_tak_pa_tiden():
    """Cursor P2-1 (#178, runde 5): dommen leses fra en FEILHÅNDTERER.
    #172 gjorde `selvrevers()` avhengig av `rollbackmaal_kompatibelt`, og
    reverseringen kjøres i nøyaktig det scenarioet der basen kan være treg
    eller uoppnåelig. Uten tak venter `psql` på default-oppførselen, og da
    står tjenestene nede så lenge — gaten som skulle sluppet reverseringen
    fram henger i stedet.

    Taket måles som ATFERD, ikke som tekst: `psql` byttes ut med et stubbet
    skript som sover langt forbi taket, og funksjonen skal likevel komme
    tilbake — fail-closed, med «umålt er ikke kompatibelt». En stub gir det
    deterministisk uten nett, der en uoppnåelig vert ville vært flakete.
    """
    import os
    import subprocess
    import tempfile
    import time
    from pathlib import Path as P
    lib = ROT / "deploy/staging/lib-opp.sh"

    with tempfile.TemporaryDirectory() as tmp:
        kat = P(tmp) / "platform/core/db/migrations"
        kat.mkdir(parents=True)
        (kat / "001_init.sql").write_text("-- t", encoding="utf-8")
        # `psql` som aldri svarer. 300 s er langt forbi både taket og enhver
        # tålmodighet et vedlikeholdsvindu har.
        bin_kat = P(tmp) / "bin"
        bin_kat.mkdir()
        stub = bin_kat / "psql"
        stub.write_text("#!/bin/sh\nsleep 300\n", encoding="utf-8")
        stub.chmod(0o755)

        start = time.monotonic()
        r = subprocess.run(
            ["bash", "-c", f'. {lib}; rollbackmaal_kompatibelt "{tmp}" '
                           '"postgresql:///uansett"'],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ,
                     PATH=f"{bin_kat}:{os.environ['PATH']}"))
        brukt = time.monotonic() - start

    assert brukt < 60, \
        f"lesingen har ikke tak på tiden — brukte {brukt:.1f} s"
    assert r.returncode != 0, "en avkuttet lesing skal aldri godkjennes"
    assert "umålt er ikke kompatibelt" in r.stdout, \
        f"avkuttet lesing skal felles som umålt: {r.stdout!r}"
    # Et tidsavbrudd navngis for seg: `timeout` dreper stille, og uten det
    # ville en drept lesing sett ut som en base som svarte tomt.
    assert "tidsavbrudd" in r.stdout, \
        f"et avkuttet kall skal navngis som tidsavbrudd: {r.stdout!r}"


def test_rollbackmaalet_tar_med_psql_feilen_i_dommen():
    """Cursor P2-2 (#178, runde 5): `2>/dev/null` svelget hver psql-feil
    likt — autentisering, nett, DNS, manglende tabell — og operatøren satt
    igjen med «versjonssettene lot seg ikke lese». Dommen var riktig
    (fail-closed), men uleselig, i nøyaktig det øyeblikket under en
    reversering der årsaken er det hen trenger.

    Målt gjennom funksjonen som kjører: en stubbet `psql` skriver en kjent
    linje til stderr og feiler, og linjen skal stå i dommen. Dommen selv
    skal fortsatt være fail-closed — feilteksten er et TILLEGG til
    «umålt er ikke kompatibelt», ikke en erstatning for den."""
    import os
    import subprocess
    import tempfile
    from pathlib import Path as P
    lib = ROT / "deploy/staging/lib-opp.sh"
    kjennemerke = "FATAL: password authentication failed for user"

    with tempfile.TemporaryDirectory() as tmp:
        kat = P(tmp) / "platform/core/db/migrations"
        kat.mkdir(parents=True)
        (kat / "001_init.sql").write_text("-- t", encoding="utf-8")
        bin_kat = P(tmp) / "bin"
        bin_kat.mkdir()
        stub = bin_kat / "psql"
        stub.write_text(f'#!/bin/sh\necho "psql: error: {kjennemerke} \\"x\\""'
                        " >&2\nexit 2\n", encoding="utf-8")
        stub.chmod(0o755)

        r = subprocess.run(
            ["bash", "-c", f'. {lib}; rollbackmaal_kompatibelt "{tmp}" '
                           '"postgresql:///uansett"'],
            capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PATH=f"{bin_kat}:{os.environ['PATH']}"))

    assert r.returncode != 0, "en mislykket lesing skal aldri godkjennes"
    assert "umålt er ikke kompatibelt" in r.stdout, \
        f"dommen skal fortsatt være fail-closed: {r.stdout!r}"
    assert kjennemerke in r.stdout, \
        f"psql-feilen skal stå i dommen, ikke svelges: {r.stdout!r}"
    # Dommen er ÉN linje: kallstedene fanger den i `$( )` og skriver den
    # inn i en setning. En flerlinjes psql-feil må derfor foldes.
    assert len(r.stdout.strip().splitlines()) == 1, \
        f"dommen skal være énlinjes: {r.stdout!r}"
