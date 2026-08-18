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
