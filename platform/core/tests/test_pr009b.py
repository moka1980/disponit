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
