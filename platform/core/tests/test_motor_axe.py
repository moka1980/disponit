"""Motor_axe — de rene hjelperne og fasitkontrakten (CI-trygt: ingen
playwright-import; browserkjøringen selv måles i staging-runden, ikke her).
"""
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
MOTOR = ROT / "platform/modules/wcag_audit/motor_axe"


def _last(navn: str):
    spec = importlib.util.spec_from_file_location(navn, MOTOR / f"{navn}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[navn] = mod
    spec.loader.exec_module(mod)
    return mod


kjor = _last("kjor")
fasitkontroll = _last("fasitkontroll")


def test_robots_parsing_og_tillatt():
    tekst = "# k\nUser-agent: googlebot\nDisallow: /alt/\n" \
            "User-agent: *\nDisallow: /privat/\nDisallow: /tmp\n"
    regler = kjor._parse_robots(tekst)
    assert [(t, m) for t, m, _ in regler] == [(False, "/privat/"),
                                              (False, "/tmp")]
    assert kjor._tillatt("/privat/hemmelig.html", regler) is False
    assert kjor._tillatt("/tmpfil", regler) is False     # prefiks, som robots
    assert kjor._tillatt("/index.html", regler) is True
    assert kjor._tillatt("/alt/x", regler) is True       # gjaldt googlebot


def test_robots_wildcard_og_sluttanker():
    """`*` og `$` er metategn, ikke bokstaver — leses de bokstavelig,
    matcher `Disallow: /privat/*.pdf$` ingenting (Codex P1)."""
    regler = kjor._parse_robots(
        "User-agent: *\nDisallow: /privat/*.pdf$\nDisallow: /*/intern\n")
    assert kjor._tillatt("/privat/rapport.pdf", regler) is False
    assert kjor._tillatt("/privat/dyp/sti/rapport.pdf", regler) is False
    assert kjor._tillatt("/privat/rapport.pdf.html", regler) is True  # $
    assert kjor._tillatt("/privat/rapport.html", regler) is True
    assert kjor._tillatt("/a/intern/x", regler) is False
    assert kjor._tillatt("/a/internt", regler) is False   # prefiks etter *
    assert kjor._tillatt("/intern", regler) is True       # * krever et ledd


def test_robots_gruppe_med_flere_user_agent_linjer():
    """`*` i en gruppe gjelder gruppa — også når den ikke står sist."""
    for tekst in ("User-agent: *\nUser-agent: googlebot\nDisallow: /privat/\n",
                  "User-agent: googlebot\nUser-agent: *\nDisallow: /privat/\n"):
        regler = kjor._parse_robots(tekst)
        assert kjor._tillatt("/privat/x", regler) is False, tekst
    # To atskilte grupper for `*` slås sammen (RFC 9309 §2.2.1); en ny
    # agentlinje ETTER en regel starter en ny gruppe.
    regler = kjor._parse_robots(
        "User-agent: *\nDisallow: /a/\n\nUser-agent: *\nDisallow: /b/\n"
        "\nUser-agent: bing\nDisallow: /c/\n")
    assert kjor._tillatt("/a/x", regler) is False
    assert kjor._tillatt("/b/x", regler) is False
    assert kjor._tillatt("/c/x", regler) is True


def test_robots_allow_vinner_paa_lengste_mønster():
    regler = kjor._parse_robots(
        "User-agent: *\nDisallow: /\nAllow: /aapen/\n")
    assert kjor._tillatt("/aapen/side.html", regler) is True
    assert kjor._tillatt("/lukket/side.html", regler) is False
    # Likt mønster, motstridende regler: Allow vinner (§2.2.2).
    likt = kjor._parse_robots("User-agent: *\nDisallow: /x\nAllow: /x\n")
    assert kjor._tillatt("/x/y", likt) is True
    # Tomme verdier er ingenting, ikke «matcher alt».
    tom = kjor._parse_robots("User-agent: *\nDisallow:\nAllow:\n")
    assert tom == [] and kjor._tillatt("/hva som helst", tom) is True
    # Regler før noen User-agent-linje tilhører ingen gruppe.
    assert kjor._parse_robots("Disallow: /privat/\n") == []


def test_bare_offentlige_maladresser_slipper_gjennom():
    """Et verifisert domene sier hvem som EIER navnet, ikke hvor det
    peker — så adressen må kontrolleres for seg (Codex P1)."""
    import ipaddress
    forbudt = ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
               "169.254.169.254",          # skymetadata
               "100.64.0.1", "0.0.0.0", "224.0.0.1",
               "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1"]
    for a in forbudt:
        assert kjor._offentlig(ipaddress.ip_address(a)) is False, a
    for a in ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"]:
        assert kjor._offentlig(ipaddress.ip_address(a)) is True, a


def test_pin_avviser_hele_forespoerselen_ved_forbudt_oppslag(monkeypatch):
    import socket as _s

    def svar(*adresser):
        return lambda *a, **k: [(_s.AF_INET, _s.SOCK_STREAM, 6, "", (ip, 443))
                                for ip in adresser]

    # Én forbudt adresse blant flere skal felle HELE oppslaget — ellers
    # er angrepet bare et spørsmål om å prøve igjen.
    monkeypatch.setattr(kjor.socket, "getaddrinfo",
                        svar("93.184.216.34", "127.0.0.1"))
    try:
        kjor._pin_mal_ip("mal.example", 443, {})
        assert False, "forbudt adresse slapp gjennom"
    except SystemExit as e:
        assert "ikke-offentlig" in str(e)

    # Rent offentlig oppslag pinnes til én adresse.
    monkeypatch.setattr(kjor.socket, "getaddrinfo", svar("93.184.216.34"))
    assert kjor._pin_mal_ip("mal.example", 443, {}) == "93.184.216.34"

    # Fixturens vertskart er det ENE unntaket, og bare for navnene det
    # selv nevner.
    monkeypatch.setattr(kjor.socket, "getaddrinfo", svar("127.0.0.1"))
    assert kjor._pin_mal_ip("fasit.example", 8443,
                            {"fasit.example": "127.0.0.1"}) == "127.0.0.1"
    try:
        kjor._pin_mal_ip("annen.example", 8443, {"fasit.example": "127.0.0.1"})
        assert False, "vertskartet gjaldt et navn det ikke nevner"
    except SystemExit:
        pass


def test_pinnen_velger_en_adresse_verten_kan_naa(monkeypatch):
    """Codex P2: `[0]` blindt er ikke et valg, det er en tilfeldighet.

    Har et offentlig navn både AAAA og A, og resolveren setter IPv6 først
    på en IPv4-only vert, ble BÅDE robots-hentingen og Chromium låst til
    en adresse verten ikke har rute til — en fullt gyldig kontroll feilet
    selv om samme navn hadde en offentlig IPv4 rett ved siden av.
    Godkjenningen er urørt: valget står bare mellom de GODKJENTE."""
    import socket as _s
    # Selve prøven rører ikke målet — en UDP-`connect` sender ingenting.
    # Måles FØR `_naabar` byttes ut under, ellers måler vi stubben.
    assert kjor._naabar("127.0.0.1", 9) is True
    assert kjor._naabar("ikke-en-adresse", 443) is False

    v6 = "2606:2800:220:1:248:1893:25c8:1946"
    monkeypatch.setattr(
        kjor.socket, "getaddrinfo",
        lambda *a, **k: [(_s.AF_INET6, _s.SOCK_STREAM, 6, "", (v6, 443, 0, 0)),
                         (_s.AF_INET, _s.SOCK_STREAM, 6, "",
                          ("93.184.216.34", 443))])
    # IPv4-only vert: den første godkjente adressen er ikke nåbar.
    monkeypatch.setattr(kjor, "_naabar",
                        lambda adresse, port: ":" not in adresse)
    assert kjor._pin_mal_ip("mal.example", 443, {}) == "93.184.216.34"
    # Er BEGGE nåbare, står resolverens egen rekkefølge (RFC 6724).
    monkeypatch.setattr(kjor, "_naabar", lambda adresse, port: True)
    assert kjor._pin_mal_ip("mal.example", 443, {}) == v6
    # Er INGEN nåbar, gjettes det ikke: feilen hører hjemme i tilkoblingen.
    monkeypatch.setattr(kjor, "_naabar", lambda adresse, port: False)
    assert kjor._pin_mal_ip("mal.example", 443, {}) == v6


def test_robots_uten_lesbart_svar_gir_ingen_crawl(monkeypatch):
    kall = {}

    def hent(status, tekst=""):
        def _h(url, pin_ip, tls_kontekst=None):
            kall["pin"] = pin_ip
            return status, tekst
        return _h

    monkeypatch.setattr(kjor, "_hent", hent(200, "User-agent: *\n"
                                                 "Disallow: /privat/\n"))
    regler, lov = kjor._robots("https://m.example", "93.184.216.34")
    assert lov is True and [m for _, m, _ in regler] == ["/privat/"]
    assert kall["pin"] == "93.184.216.34"      # hentingen bruker pinnen
    assert kjor._robots("https://m.example", "1.2.3.4")[1] is True
    monkeypatch.setattr(kjor, "_hent", hent(404))
    assert kjor._robots("https://m.example", "1.2.3.4") == ([], True)
    for status in (301, 302, 503, 500):
        monkeypatch.setattr(kjor, "_hent", hent(status))
        assert kjor._robots("https://m.example", "1.2.3.4") == ([], False), \
            status

    def sprekk(*a, **k):
        raise OSError("nede")
    monkeypatch.setattr(kjor, "_hent", sprekk)
    assert kjor._robots("https://m.example", "1.2.3.4") == ([], False)


def test_robots_over_lesetaket_stenger_crawlen(monkeypatch):
    """Codex P1: en robots vi bare fikk BEGYNNELSEN av har ikke sagt ja.

    `_hent` leste `LESETAK` bytes og returnerte prefikset som om det var
    hele dokumentet. Reglene ligger i vilkårlig rekkefølge i fila, så det
    var tilfeldig hvilke forbud som havnet innenfor grensen — og hver
    `Disallow` etter den ble stille borte, altså en sti målet eksplisitt
    forbød, hentet."""
    class Svar:
        status = 200

        def __init__(self, n):
            self.data = b"x" * n

        def read(self, n):
            return self.data[:n]

    class Conn:
        n = 0

        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Svar(Conn.n)

        def close(self):
            pass

    monkeypatch.setattr(kjor.http.client, "HTTPSConnection", Conn)
    # Nøyaktig på taket er fortsatt et helt svar.
    Conn.n = kjor.LESETAK
    assert kjor._hent("https://m.example/robots.txt", "1.2.3.4")[0] == 200
    # Én byte over: ingen tolkning av prefikset.
    Conn.n = kjor.LESETAK + 1
    try:
        kjor._hent("https://m.example/robots.txt", "1.2.3.4")
        raise AssertionError("et avkortet svar ble lest som et helt svar")
    except kjor._Avkortet:
        pass
    # Og `_robots` behandler den som alle andre uleste robotser: ingen
    # crawl — som så slår `avkortet` på, se testen over.
    assert kjor._robots("https://m.example", "1.2.3.4") == ([], False)


def test_enkeltside_henter_ikke_robots():
    """Codex P2: en `enkeltside`-kontroll skal ikke røre `/robots.txt`.

    Kallet var ubetinget, så hver enkeltside-bestilling sendte en ekstra
    GET mot en sti kunden aldri pekte på — en forespørsel som per
    definisjon ikke kunne endre noe, siden `maks_sider == 1` og ingen
    lenker følges. Bestillingen var autorisert og bokført som ÉN sides
    inspeksjon, men ble to utenfra synlige treff.

    Hentingen lever inne i `main()` bak playwright, så porten måles på
    kilden: `_robots` skal stå under `if maks_sider > 1`."""
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert kilde.count("_robots(origin") == 1
    hode = kilde.split("_robots(origin", 1)[0]
    assert hode.rstrip().endswith("disallow, krype_lov ="), hode[-120:]
    assert hode.rstrip().rsplit("\n", 2)[1].strip() == "if maks_sider > 1:", \
        "robots-hentingen står ikke bak crawl-betingelsen"


def test_lenkenormalisering_er_lukket():
    n = kjor._normaliser_lenke
    o = "http://127.0.0.1:8093"
    assert n(o, f"{o}/index.html", "/om.html") == f"{o}/om.html"
    assert n(o, f"{o}/index.html", "om.html") == f"{o}/om.html"
    assert n(o, f"{o}/index.html", "/om.html#seksjon") == f"{o}/om.html"
    assert n(o, f"{o}/index.html", "https://annen.example/") is None
    assert n(o, f"{o}/index.html", "mailto:x@y") is None
    # Query BEHOLDES: den er sideidentitet (Codex P2). Rapportformen uten
    # query lages ett nivå opp, av `rapport._delt_url`.
    assert n(o, f"{o}/index.html", "/sok?q=1") == f"{o}/sok?q=1"
    assert n(o, f"{o}/p", "/p?id=1") != n(o, f"{o}/p", "/p?id=2")
    assert n(o, f"{o}/index.html", "/sok?q=1#treff") == f"{o}/sok?q=1"


def test_eksempeltaket_slaar_avkortet_paa():
    """Eksempeltaket kapper evidens motoren ALT har observert, og det gjorde
    det stille (Codex P2): en regel med mer enn `MAKS_EKSEMPLER` feilende
    noder ga `avkortet.truffet: false`. Byggerens egen eksempeltelling kan
    ikke fange det — den ser bare den kappede lista, så tallet ligger per
    definisjon PÅ taket, aldri over.

    Takene må dessuten være IDENTISKE med byggerens: kappet motoren
    hardere, ville byggeren aldri fått se at noe ble kappet."""
    sys.path.insert(0, str(ROT / "platform"))
    from modules.wcag_audit import rapport
    assert kjor.MAKS_EKSEMPLER == rapport.MAKS_EKSEMPLER
    assert kjor.MAKS_SELEKTOR == rapport.MAKS_SELEKTOR

    # Signalet slik motoren regner det ut, på kilden: begge takene, med
    # crawltaket først når begge er truffet.
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "sider_avkortet = bool(ko)" in kilde
    assert "eksempler_avkortet = max(" in kilde
    assert "[True, MAKS_EKSEMPLER, eksempler_avkortet]" in kilde
    assert "truffet = bool(ko)" not in kilde

    # Og at et kappet funn faktisk er kjennbart: `antall` teller alle
    # nodene, `eksempler` bærer maks ti — differansen ER signalet.
    funn = {"r": {"antall": 25, "eksempler": ["x"] * kjor.MAKS_EKSEMPLER},
            "s": {"antall": 3, "eksempler": ["y"] * 3}}
    assert max((f["antall"] for f in funn.values()
                if f["antall"] > len(f["eksempler"])), default=0) == 25
    assert max((f["antall"] for f in [funn["s"]]
                if f["antall"] > len(f["eksempler"])), default=0) == 0


def test_selektorkuttet_slaar_avkortet_paa():
    """Codex P2: det tredje taket var det eneste som ikke sa fra.

    For et funn med ÉN node hvis selektor er over `MAKS_SELEKTOR` tegn er
    både `antall` og `len(eksempler)` 1, så eksempelregnskapet ser
    ingenting — mens eksempelet i den promoterte rapporten er en avhogd,
    ofte syntaktisk ødelagt selektor under påstanden `avkortet: false`.
    Byggeren kan ikke fange det heller: den kapper på NØYAKTIG samme tall,
    så lista den ser ligger alltid på grensen, aldri over.

    Kappingen lever inne i `main()` bak playwright, så porten måles på
    kilden — og på at rekkefølgen mellom takene er skadens."""
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "if len(sel) > MAKS_SELEKTOR:" in kilde
    assert "[True, MAKS_SELEKTOR, selektor_avkortet]" in kilde
    # Rekkefølgen: sider → robots → eksempler → selektor. Hele SIDER som
    # mangler er mer enn et funn med flere forekomster enn de viste, og
    # begge er mer enn ett eksempel som peker upresist.
    kjede = kilde.split("sider_avkortet = bool(ko)", 1)[1]
    rekkefolge = [kjede.index(nokkel) for nokkel in (
        "if sider_avkortet:", "elif robots_stengte:",
        "elif eksempler_avkortet:", "elif selektor_avkortet:")]
    assert rekkefolge == sorted(rekkefolge), kjede[:400]


def test_nettleserkonteksten_er_den_som_attesteres():
    """Codex P2: serverkonteksten attesterte en tidssone ingen satte.

    Hver kjøring føres som `timezone: Europe/Oslo`, men kontekstobjektet
    fikk bare `viewport` og `locale` — Chromium brukte containerens egen
    tidssone. En side som rendrer innhold eller tilgjengelighetstilstand ut
    fra `Date`/`Intl`/lokal tid kunne dermed bli undersøkt i et annet miljø
    enn den promoterte rapporten oppgir.

    Verdiene bindes her, samme grep som `MAKS_EKSEMPLER` og
    `VERT_MONSTER`: to skrivemåter av samme påstand er ingen påstand."""
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "timezone_id=TIDSSONE" in kilde
    assert "locale=LOCALE" in kilde
    assert "viewport=dict(VIEWPORT)" in kilde

    sjekk = (ROT / "deploy/staging/wcag-staging-sjekkliste.py").read_text(
        encoding="utf-8")
    vp = f'{kjor.VIEWPORT["width"]}x{kjor.VIEWPORT["height"]}'
    for felt, verdi in (("timezone", kjor.TIDSSONE), ("locale", kjor.LOCALE),
                        ("viewport", vp)):
        assert f'"{felt}": "{verdi}"' in sjekk, \
            f"serverkonteksten oppgir ikke {felt}={verdi}"
    # ... og ingen ANNEN verdi står igjen for de samme feltene.
    import re
    for felt, verdi in (("timezone", kjor.TIDSSONE), ("locale", kjor.LOCALE),
                        ("viewport", vp)):
        funnet = set(re.findall(rf'"{felt}": "([^"]*)"', sjekk))
        assert funnet == {verdi}, (felt, funnet)


def test_varigheten_dekker_oppslaget_og_robots():
    """Codex P2: klokka startet etter oppslaget og robots-hentingen.

    `varighet_ms` er timingevidensen i den promoterte rapporten. Bare
    robots kan alene bruke ti sekunder (`_hent`-fristen), og DNS kommer i
    tillegg — arbeid som er like synlig utenfra som selve sidelastingen,
    men som ikke fantes i tallet."""
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert kilde.count("start = time.monotonic()") == 1
    assert kilde.index("start = time.monotonic()") \
        < kilde.index("mal_pin = _pin_mal_ip(") \
        < kilde.index("_robots(origin") \
        < kilde.index("sync_playwright() as pw")
    # ... og `_axe_kilde()` er UTENFOR: den rører aldri målet.
    assert kilde.index("axe_js = _axe_kilde()") \
        < kilde.index("start = time.monotonic()")


def test_uleselig_robots_melder_seg_som_avkorting():
    """Codex P2: en uleselig robots gjør et nettsted-oppdrag til én side.

    Lenkeuttrekket slås av, køen blir tom, og `sider_avkortet` er derfor
    usann — rapporten meldte «alt kom med» for en kontroll som dekket én
    av inntil femti sider, og den bærer ikke det bestilte `omfang` noe
    annet sted. Et forbigående driftsminutt hos kunden ga altså promotert
    evidens som ser komplett ut.

    Trippelen skal si «kappet ved 1 av det bestilte»."""
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "robots_stengte = True" in kilde
    assert "elif robots_stengte:" in kilde
    assert "avkortet = [True, maks_sider, bestilt_maks]" in kilde
    # Det bestilte taket tas vare på FØR robots får senke det.
    assert kilde.index("bestilt_maks = maks_sider") \
        < kilde.index("maks_sider = 1      # robots")
    # Crawltaket har forsett foran robots-grenen: er BEGGE sanne, er det
    # sidene som mangler leseren trenger å vite om først.
    assert kilde.index("if sider_avkortet:") \
        < kilde.index("elif robots_stengte:") \
        < kilde.index("elif eksempler_avkortet:")


def test_blokkert_vert_blir_alltid_baerbar_for_rapporten():
    """En blokkering skal bli en DEKNINGSBEGRENSNING, aldri et avbrutt
    oppdrag (Codex P2). `rapport.bygg` krever et prikket vertsnavn av hver
    blokkerte post, så en korrekt blokkert forespørsel til `localhost` eller
    en IPv6-literal gjorde hele den ellers vellykkede kontrollen om til
    `motor_avbrutt`."""
    rv = kjor._rapportvert
    # Prikkede navn og IPv4 går urørt gjennom.
    assert rv("ekstern-cdn.example") == "ekstern-cdn.example"
    assert rv("EKSTERN-CDN.Example") == "ekstern-cdn.example"
    assert rv("169.254.169.254") == "169.254.169.254"
    # Enkeltetikett og IPv6 beholdes, men i RFC 2606-navnerommet.
    assert rv("localhost") == "localhost.enkeltetikett.invalid"
    # UTFOLDET, ikke komprimert: `::1` ville gitt etiketten `--1`, og en
    # etikett kan ikke begynne med bindestrek.
    assert rv("[::1]") == ("0000-" * 7 + "0001") + ".ipv6.invalid"
    assert rv("[2001:db8::1]") == \
        "2001-0db8-0000-0000-0000-0000-0000-0001.ipv6.invalid"
    # Uleselig blir en rad, ikke en forsvunnet rad.
    assert rv("") == "uleselig.blokkert.invalid"
    assert rv("under_strek.example") == "uleselig.blokkert.invalid"
    assert rv("a" * 300 + ".example") == "uleselig.blokkert.invalid"

    # PORTEN: hver form over må passere rapportbyggerens EGET mønster.
    # Driver de to fra hverandre, er saneringen her uten virkning.
    sys.path.insert(0, str(ROT / "platform"))
    from modules.wcag_audit import rapport
    assert rapport._VERT.pattern == kjor.VERT_MONSTER.pattern
    for raa in ("localhost", "[::1]", "[2001:db8::1]", "", "169.254.169.254",
                "under_strek.example", "ekstern-cdn.example"):
        v = rv(raa)
        assert rapport._VERT.match(v) and len(v) <= 253, (raa, v)


def test_origin_er_kanonisk_i_baade_vakten_og_lenkefilteret():
    """Rå strengsammenligning av `scheme://netloc` er ikke origin-regelen
    (Codex P2): den underforståtte porten, store/små bokstaver og
    brukerinfo hører ikke med, og forskjellen slo BEGGE veier — legitime
    sider falt ut av crawlen, og legitime forespørsler ble blokkert og talt
    som dekningsbegrensning."""
    o, u = kjor._origin, urllib.parse.urlsplit
    assert o(u("https://example.com/x")) == "https://example.com"
    assert o(u("https://example.com:443/x")) == "https://example.com"
    assert o(u("https://EXAMPLE.com/x")) == "https://example.com"
    assert o(u("https://bruker:pw@example.com/x")) == "https://example.com"
    assert o(u("http://example.com:80/x")) == "http://example.com"
    # Ikke-standard port BLIR stående — den er en del av origin.
    assert o(u("https://example.com:8443/x")) == "https://example.com:8443"
    assert o(u("http://127.0.0.1:8093/x")) == "http://127.0.0.1:8093"
    # ws/wss måles som http/https, med samme standardporter (RFC 6455 §3).
    assert o(u("wss://example.com/s")) == "https://example.com"
    assert o(u("wss://example.com:443/s")) == "https://example.com"
    assert o(u("ws://example.com:80/s")) == "http://example.com"
    assert o(u("wss://example.com:9001/s")) == "https://example.com:9001"
    # IPv6-literalen beholder klammene, ellers er den ikke en origin.
    assert o(u("https://[2001:db8::1]:8443/x")) == "https://[2001:db8::1]:8443"
    # Uleselig: "" matcher aldri målets origin, som alltid har en vert.
    assert o(u("https://example.com:99999/x")) == ""
    assert o(u("mailto:x@y")) == ""

    # Og porten der funnet ble målt: lenkefilteret.
    n = kjor._normaliser_lenke
    assert n("https://example.com", "https://example.com/",
             "https://example.com:443/produkt") == \
        "https://example.com/produkt"
    assert n("https://example.com", "https://example.com/",
             "https://EXAMPLE.com/produkt") == "https://example.com/produkt"
    assert n("https://example.com", "https://example.com/",
             "https://example.com:8443/produkt") is None


def test_crawlen_rapporterer_og_loeser_mot_den_landede_url_en():
    """Rapporten skal navngi siden vi FAKTISK kontrollerte (Codex P1).

    `page.goto` følger omdirigeringer og axe kjører mot det endelige
    dokumentet, men både `sider[].url` og lenkeoppløsningen brukte den
    BESTILTE URL-en: `/gammel` → `/ny/` ga evidens for `/gammel`, og
    `a.html` i det landede dokumentet ble crawlet som `/a.html`.

    Crawlen lever inne i `main()` bak playwright, så porten måles på
    kilden: `page.url` skal være det som bæres videre, ikke `url`."""
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "landet = _normaliser_lenke(origin, url, page.url)" in kilde
    assert '"url": faktisk' in kilde
    assert "_normaliser_lenke(origin, faktisk, href" in kilde
    assert '"url": url,' not in kilde, \
        "den bestilte URL-en skal ikke lenger rapporteres som siden"

    # Og oppløsningen den porten hviler på: en relativ href måles mot det
    # landede dokumentet, ikke mot adressen vi spurte om.
    n, o = kjor._normaliser_lenke, "https://x.example"
    assert n(o, f"{o}/ny/", "a.html") == f"{o}/ny/a.html"
    assert n(o, f"{o}/gammel", "a.html") == f"{o}/a.html"


def test_robots_gjelder_ogsaa_omdirigeringsmaalet():
    """Codex P1: et 30x er ingen lenke, og filteret sto bare på lenkene.

    En tillatt `/gaa` som svarer 301 til en `Disallow`-sti fortsatte i
    vakten fordi målet var samme origin — og axe kjørte mot den forbudte
    siden, som etter landings-fiksen til og med ble navngitt i rapporten.
    Vakten lever inne i `main()` bak playwright, så porten måles på kilden
    pluss den delte stiformen begge sidene av regelen bruker."""
    regler = kjor._parse_robots("User-agent: *\nDisallow: /privat/\n"
                                "Disallow: /*?hemmelig\n")
    u = urllib.parse.urlsplit
    assert not kjor._tillatt(
        kjor._robotsti(u("https://x.example/privat/side")), regler)
    assert kjor._tillatt(
        kjor._robotsti(u("https://x.example/gaa")), regler)
    # Query-en er med — ellers er `Disallow: /*?…` en regel uten virkning.
    assert not kjor._tillatt(
        kjor._robotsti(u("https://x.example/s?hemmelig=1")), regler)
    # Tom sti er `/`, ikke "".
    assert kjor._robotsti(u("https://x.example")) == "/"

    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "req.is_navigation_request()" in kilde
    assert "not _tillatt(_robotsti(u), disallow)" in kilde
    # Og stiformen er DELT: bygges den to steder, kan de gli fra hverandre.
    assert kilde.count("_robotsti(") == 3, \
        "stiformen skal komme fra én funksjon, brukt begge steder"


def test_robots_gjelder_hver_navigasjon_ikke_bare_omdirigeringer():
    """Codex P1: en navigasjon trenger ingen omdirigering for å oppstå.

    Vakten målte bare forespørsler med `redirected_from`, men
    `location.replace('/privat/side')`, et `window.open`, en `<iframe
    src=…>` og en `<meta refresh>` er alle navigasjoner UTEN forgjenger.
    Hver av dem hentet den forbudte siden, og for hovedrammens del kunne
    axe ende opp med å kontrollere den — samme vei rundt porten som
    omdirigeringen, åpnet med et annet verktøy.

    Unntaket skal være nøyaktig ÉN URL: den bestilte `mal_url`, som er
    kundens eget valg. Vakten lever inne i `main()` bak playwright, så
    porten måles på kilden."""
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "req.redirected_from" not in kilde, \
        "robots-vakten skal ikke lenger begrense seg til omdirigeringer"
    vakt = kilde.split("def vakt(route):", 1)[1].split("def vakt_ws", 1)[0]
    assert "krype and req.is_navigation_request()" in vakt
    assert "!= mal_kanonisk" in vakt

    # Unntaket er den BESTILTE siden i lenkefilterets egen form, bygget
    # ÉTT sted — ellers kan sammenligningen gli på en standardport eller
    # et fragment mens køfrøet står i den andre formen.
    assert kilde.count("mal_kanonisk = ") == 1
    assert "oppdaget = {mal_kanonisk}" in kilde
    n, o = kjor._normaliser_lenke, "https://x.example"
    assert n(o, f"{o}:443/side#topp", f"{o}:443/side#topp") == f"{o}/side"


def test_bare_lesende_metoder_slipper_ut_av_kontrollen():
    """Codex P1: egressvakten målte bare origin, og origin er ikke metode.

    En kontrollert side som kjørte `fetch(..., {method: "POST"})` eller
    `navigator.sendBeacon` fikk skrive mot sitt eget nettsted — med cookies
    satt under navigasjonen — mens modulkontrakten sier `ekstern_lesing`:
    ingen sideeffekt hos målet."""
    assert kjor.LESEMETODER == frozenset({"GET", "HEAD"})
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "req.method.upper() not in LESEMETODER" in kilde
    # Blokkeringen TELLES, som alt annet blokkert — den skal ikke være en
    # stille avvisning.
    metodedel = kilde.split("not in LESEMETODER", 1)[1].split("return", 1)[0]
    assert "tell(" in metodedel and 'route.abort("blockedbyclient")' \
        in metodedel


def test_websocket_kanalen_lukkes_helt():
    """Codex P1: en websocket-ramme har ingen HTTP-metode.

    Første utgave avskar websockets, men koblet gjennom hver av dem som
    gikk til målets EGEN origin. `LESEMETODER` er kontraktens skille
    mellom å lese og å skrive, og det skillet finnes ikke i en
    websocket: kanalen er toveis fra første byte. En kontrollert side
    kunne dermed sende tilstandsendrende protokollmeldinger til
    `wss://<mål>/…` — med cookies satt under navigasjonen — og omgå
    nøyaktig den begrensningen HTTP-vakten håndhever, i en motor hvis
    handling er klassifisert `ekstern_lesing`.

    Vakten lever inne i `main()` bak playwright, så porten måles på
    kilden."""
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    ws_del = kilde.split("def vakt_ws(ws):", 1)[1].split("ctx.route(", 1)[0]
    assert "connect_to_server" not in ws_del, \
        "ingen websocket skal kobles gjennom, heller ikke til samme origin"
    # Lukkingen TELLES som alt annet blokkert: den er en
    # dekningsbegrensning rapporten skal navngi, ikke en stille avvisning.
    assert "tell(" in ws_del and "ws.close()" in ws_del
    # Og avskjæringen SELV er fortsatt en forutsetning, ikke en påstand.
    assert 'hasattr(ctx, "route_web_socket")' in kilde
    assert 'ctx.route_web_socket("**/*", vakt_ws)' in kilde


def test_kravsett_og_alvorlighet_dekker_kontrakten():
    # Enum-ene speiler rapportskjemaet — driver de fra hverandre, produserer
    # motoren verdier skjemavalideringen avviser.
    assert set(kjor.KRAVSETT_TAGS) == {"wcag21_aa"}
    assert set(kjor.ALVORLIGHET.values()) == {"kritisk", "alvorlig",
                                              "moderat", "lav"}
    assert set(kjor.ART.values()) <= {"stilark", "font", "skript", "bilde"}


def test_axe_pinnen_er_ekte_hex():
    assert len(kjor.AXE_SHA256) == 64
    int(kjor.AXE_SHA256, 16)
    assert kjor.AXE_VERSJON in kjor.AXE_URL


def test_fasitkontroll_finner_hver_avviksklasse():
    fasit = json.loads(
        (ROT / "platform/modules/wcag_audit/testnettsted/fasit.json")
        .read_text(encoding="utf-8"))
    s = fasit["scenarier"]["enkeltside"]
    # Perfekt motorutdata konstruert FRA fasiten → null avvik.
    motor = {
        "funn": [{"regel_id": rid, "alvorlighet": v["alvorlighet"],
                  "antall": v["antall"], "eksempler": []}
                 for rid, v in s["funn"].items()],
        "blokkert": [{"vert": vert, "art": art, "antall": n}
                     for vert, arter in s["blokkert"].items()
                     for art, n in arter.items()],
        "avkortet": list(s["avkortet"]),
        "sider": [{"url": "http://t/index.html", "status": "ok"}]
                 * s["sider_ok"],
    }
    assert fasitkontroll.avvik(s, motor) == []
    # …og hver klasse av avvik navngis.
    b = json.loads(json.dumps(motor))
    b["funn"][0]["antall"] += 1
    assert any(a.startswith("funn:") for a in fasitkontroll.avvik(s, b))
    b = json.loads(json.dumps(motor))
    b["blokkert"] = []
    assert any(a.startswith("blokkert:") for a in fasitkontroll.avvik(s, b))
    b = json.loads(json.dumps(motor))
    b["avkortet"] = [True, 1, 2]
    assert any(a.startswith("avkortet:") for a in fasitkontroll.avvik(s, b))
    b = json.loads(json.dumps(motor))
    b["sider"][0]["status"] = "feilet"
    assert any(a.startswith("sider:") for a in fasitkontroll.avvik(s, b))


def test_fasiten_er_konsistent_med_seg_selv():
    """Avkortingsregnskapet i fasiten: 14 = 4 besøkte + 9 i kø + 1
    query-lenke, og robots-siden er aldri en del av regnskapet."""
    fasit = json.loads(
        (ROT / "platform/modules/wcag_audit/testnettsted/fasit.json")
        .read_text(encoding="utf-8"))
    s = fasit["scenarier"]["nettsted_maks4"]
    truffet, tak, verdi = s["avkortet"]
    assert truffet is True and tak == s["payload"]["maks_sider"]
    assert verdi == 14
    assert "/privat/hemmelig.html" not in s["_crawlrekkefolge"]
    sider = ROT / "platform/modules/wcag_audit/testnettsted/sider"
    assert (sider / "privat/hemmelig.html").exists()
    assert "Disallow: /privat/" in (sider / "robots.txt").read_text(
        encoding="utf-8")
    # Query-lenken fasiten teller MÅ finnes i fixturen, på en side som
    # faktisk besøkes — ellers måler ikke runden det den sier den måler.
    kontakt = (sider / "kontakt.html").read_text(encoding="utf-8")
    assert 'href="/sok?q=' in kontakt
    assert "/kontakt.html" in s["_crawlrekkefolge"]


def test_basisimaget_kan_ikke_bygges_upinnet():
    """Kommentaren i Dockerfila sa «PINNET på digest» mens FROM brukte en
    mutabel tagg (Codex P2). Pinnen skal være håndhevet, ikke påstått:
    ARG-en har ingen standardverdi, så et bygg uten den feiler."""
    import re
    d = (MOTOR / "Dockerfile").read_text(encoding="utf-8")
    linjer = [ln.strip() for ln in d.splitlines()
              if ln.strip() and not ln.lstrip().startswith("#")]
    assert "ARG PLAYWRIGHT_BASIS" in linjer, \
        "ARG-en må stå uten standardverdi (ingen `=`)"
    froms = [ln for ln in linjer if ln.upper().startswith("FROM ")]
    assert froms == ["FROM ${PLAYWRIGHT_BASIS}"], froms
    # Ingen mutabel tagg-referanse igjen noe sted i byggetrinnene.
    assert not any(re.search(r"mcr\.microsoft\.com/\S+:", ln)
                   for ln in linjer), linjer

    # Pinnefila finnes, og bygg.sh leser NØYAKTIG den — ellers kan de to
    # drive fra hverandre og bygget bli pinnet til noe annet enn det
    # repoet oppgir.
    pinne = MOTOR / "basis-digest.txt"
    assert pinne.exists()
    bygg = (MOTOR / "bygg.sh").read_text(encoding="utf-8")
    assert "basis-digest.txt" in bygg
    assert "--build-arg PLAYWRIGHT_BASIS=" in bygg
    verdi = "".join(ln.split("#", 1)[0].strip()
                    for ln in pinne.read_text(encoding="utf-8").splitlines())
    assert "@sha256:" in verdi, "pinnefila må navngi en digest, ikke en tagg"

    # ... og pinnen skal kunne FYLLES, ikke bare avvises (Codex P1): en
    # håndhevet pinne uten verdi er en stengt dør, og en fersk utrulling
    # kunne ikke bygge motoren i det hele tatt. `bygg.sh pin` henter
    # digesten for TAGGEN REPOET SELV navngir — ett sted, så
    # pinnekommandoen ikke kan pinne noe annet enn det fila dokumenterer.
    assert 'if [ "${1:-}" = "pin" ]' in bygg
    assert "BASISTAGG=" in bygg
    tagger = set(re.findall(r"mcr\.microsoft\.com/playwright/python:[\w.-]+",
                            bygg))
    assert len(tagger) == 1, tagger
    assert 'docker pull "$BASISTAGG"' in bygg
    # `pin` skriver bare fila — den bygger ikke, og committer ikke.
    pin_del = bygg.split('= "pin" ]', 1)[1].split("exit 0", 1)[0]
    assert "docker build" not in pin_del and "git " not in pin_del


def test_motorimaget_har_playwright_pakken():
    """Codex P1: basisimaget bærer nettleserne, ikke nødvendigvis pakken.

    `kjor.py` gjør `from playwright.sync_api import sync_playwright` — uten
    pakken feiler HVER kjøring, og feilen ville dukket opp første gang en
    kunde bestilte en kontroll, ikke i bygget. Versjonen skal dessuten være
    browser-imagets egen, avledet av `BASISTAGG`: to versjonsnumre som må
    endres i takt, er ett som blir glemt."""
    import re
    d = (MOTOR / "Dockerfile").read_text(encoding="utf-8")
    bygg = (MOTOR / "bygg.sh").read_text(encoding="utf-8")
    assert "ARG PLAYWRIGHT_PAKKE" in d
    assert 'playwright==${PLAYWRIGHT_PAKKE}' in d
    # Importen kjøres i BYGGET: er pakken ikke brukbar, skal bygget feile.
    assert "from playwright.sync_api import sync_playwright" in d
    # Ingen versjon skrevet inn for hånd i Dockerfila.
    assert not re.search(r"playwright==\d", d), d
    assert "--build-arg PLAYWRIGHT_PAKKE=" in bygg
    assert 'pakkeversjon="${BASISTAGG##*:v}"' in bygg
    # ... og motoren importerer faktisk det pakken gir.
    kilde = (MOTOR / "kjor.py").read_text(encoding="utf-8")
    assert "from playwright.sync_api import sync_playwright" in kilde


def test_motorfilene_er_lesbare_for_den_ubetrodde_brukeren():
    """Codex P1: `ADD` fra en URL gir root-eid 0600, og imaget kjører som
    `pwuser`. `_axe_kilde()` kunne da ikke lese `AXE_STI`, og motoren døde
    før Playwright ble startet — hver eneste kjøring."""
    d = (MOTOR / "Dockerfile").read_text(encoding="utf-8")
    assert "chmod 0444 /motor/axe.min.js" in d
    assert "chmod 0444 /motor/kjor.py" in d
    # Modusen må settes FØR privilegiene slippes, ellers hjelper den ikke.
    assert d.index("chmod 0444") < d.index("USER pwuser")


# ---------------------------------------------------------------------------
# Kontraktsdokumentene (kontrakt/): proveniens som ikke får drive fra koden
# ---------------------------------------------------------------------------

def test_kontraktsdokumentets_hasher_matcher_skjemafilene():
    """KONTRAKT.md navngir payload-/kvitteringsskjemaets sha256 — driver
    dokument og fil fra hverandre, registreres feil proveniens immutabelt."""
    import hashlib
    kdir = ROT / "platform/modules/wcag_audit/kontrakt"
    md = (kdir / "KONTRAKT.md").read_text(encoding="utf-8")
    for fil, felt in (("payload-skjema.json", "payload_schema_hash"),
                      ("kvittering-skjema.json", "kvittering_schema_hash")):
        h = hashlib.sha256((kdir / fil).read_bytes()).hexdigest()
        assert f"**{felt}**: `{h}`" in md, \
            f"{felt} i KONTRAKT.md matcher ikke {fil}"


def test_kvitteringsskjemaet_speiler_controllerens_feilkoder():
    kdir = ROT / "platform/modules/wcag_audit/kontrakt"
    skjema = json.loads((kdir / "kvittering-skjema.json")
                        .read_text(encoding="utf-8"))
    i_skjema = set(skjema["properties"]["feilkode"]["enum"])
    import re
    kode = (ROT / "platform/modules/wcag_audit/controller.py") \
        .read_text(encoding="utf-8")
    i_koden = set(re.findall(r'"feilkode": "([a-z_]+)"', kode))
    assert i_skjema == i_koden, (i_skjema ^ i_koden)


def test_payloadskjemaet_speiler_oppdragskontrakten():
    import oppdragskontrakt
    kdir = ROT / "platform/modules/wcag_audit/kontrakt"
    skjema = json.loads((kdir / "payload-skjema.json")
                        .read_text(encoding="utf-8"))
    t = oppdragskontrakt.OPPDRAGSTYPER["kontroll.wcag.nettsted"]
    assert set(skjema["properties"]) == set(t.felter)
    assert set(skjema["required"]) == set(t.felter), \
        "normaliseringen fyller alltid alle fire feltene"
    assert skjema["additionalProperties"] is False
