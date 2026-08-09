"""PR-010: den IP-pinnede SSRF-transporten (v5 §2 + v6 §1).

Rebinding-testene er kjernen: DNS svarer offentlig ved validering og
privat ved neste oppslag → ingen forbindelse. Resolveren monkeypatches så
matrisen kjøres uten ekte DNS eller nett.
"""
import ipaddress

import pytest

from .conftest import CORE  # noqa: F401  (setter sys.path)
from api import ssrf


def _patch_resolv(monkeypatch, svar):
    """svar: dict host -> liste av IP-strenger, ELLER en callable(host)."""
    def fake(host, port):
        v = svar(host) if callable(svar) else svar[host]
        return [ipaddress.ip_address(ip) for ip in v]
    monkeypatch.setattr(ssrf, "_resolv", fake)


# ---------------------------------------------------------------------------
# Forbudt-klassifisering: allowlist-prinsippet (V1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ip,forbudt", [
    ("8.8.8.8", False), ("1.1.1.1", False),
    ("2606:4700:4700::1111", False),
    ("127.0.0.1", True), ("10.0.0.1", True), ("192.168.0.5", True),
    ("172.16.0.1", True), ("169.254.169.254", True), ("169.254.1.1", True),
    ("100.64.0.1", True),                         # CGNAT
    ("192.0.2.1", True),                          # dokumentasjonsnett
    ("::1", True), ("fe80::1", True), ("fd00:ec2::254", True),
    ("fc00::1", True),                            # unique local
    ("::ffff:10.0.0.1", True),                    # IPv4-mapped privat
    ("::ffff:169.254.169.254", True),             # IPv4-mapped metadata
    ("0.0.0.0", True), ("224.0.0.1", True),       # unspecified, multicast
])
def test_forbudt_er_allowlist_prinsipp(ip, forbudt):
    assert ssrf._forbudt(ipaddress.ip_address(ip)) is forbudt


# ---------------------------------------------------------------------------
# Alternative IP-notasjoner avvises (v6 §1 pkt. 1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "2130706433",        # desimal 127.0.0.1
    "0x7f.0.0.1",        # heks
    "0177.0.0.1",        # oktal
    "127.1",             # avkortet
    "127.0.0.1.",        # etterfølgende punktum (som IP-literal)
    "010.0.0.1",         # ledende null
])
def test_alternative_ip_notasjoner_er_ikke_literaler(host):
    # Faller til hostname-veien; der finnes ingen slik host → SsrfAvvist.
    assert ssrf._ip_literal(host) is None


def test_kanonisk_ip_literal_godtas():
    assert str(ssrf._ip_literal("127.0.0.1")) == "127.0.0.1"
    assert str(ssrf._ip_literal("8.8.8.8")) == "8.8.8.8"


# ---------------------------------------------------------------------------
# valider_og_pin: den fulle porten
# ---------------------------------------------------------------------------

def test_global_host_pinnes(monkeypatch):
    _patch_resolv(monkeypatch, {"idp.example.com": ["8.8.8.8"]})
    assert ssrf.valider_og_pin("https", "idp.example.com", 443) == "8.8.8.8"


def test_privat_host_avvises(monkeypatch):
    _patch_resolv(monkeypatch, {"intern.example": ["10.0.0.5"]})
    with pytest.raises(ssrf.SsrfAvvist, match="forbudt"):
        ssrf.valider_og_pin("https", "intern.example", 443)


def test_metadata_ip_avvises(monkeypatch):
    _patch_resolv(monkeypatch, {"evil.example": ["169.254.169.254"]})
    with pytest.raises(ssrf.SsrfAvvist):
        ssrf.valider_og_pin("https", "evil.example", 443)


def test_blandet_offentlig_og_privat_avvises_helt(monkeypatch):
    """v6 §1 pkt. 3 + bindende test: en hostname som resolver til BÅDE
    offentlig og privat IP avvises — vi plukker aldri den lovlige."""
    _patch_resolv(monkeypatch, {"rebind.example": ["8.8.8.8", "127.0.0.1"]})
    with pytest.raises(ssrf.SsrfAvvist, match="forbudt"):
        ssrf.valider_og_pin("https", "rebind.example", 443)


def test_rebinding_offentlig_ved_validering_privat_ved_neste(monkeypatch):
    """Kjernetesten: DNS svarer OFFENTLIG når vi validerer og PRIVAT ved
    neste oppslag. Fordi vi PINNER den validerte IP-en og aldri resolver
    på nytt, opprettes ingen forbindelse til den private."""
    svar = iter([["8.8.8.8"], ["127.0.0.1"]])
    _patch_resolv(monkeypatch, lambda host: next(svar))
    # Første kall validerer og pinner den offentlige.
    assert ssrf.valider_og_pin("https", "rebind.example", 443) == "8.8.8.8"
    # Et ANDRE oppslag ville gitt privat — men transporten gjør det aldri;
    # den bruker den pinnede IP-en fra første validering.


def test_ikke_https_avvises(monkeypatch):
    _patch_resolv(monkeypatch, {"idp.example": ["8.8.8.8"]})
    with pytest.raises(ssrf.SsrfAvvist, match="HTTPS"):
        ssrf.valider_og_pin("http", "idp.example", 80)


# ---------------------------------------------------------------------------
# Staging-allowlist (eksakt scheme,host,port,CIDR) — test-IdP
# ---------------------------------------------------------------------------

def test_staging_allowlist_slipper_gjennom_test_idp(monkeypatch):
    _patch_resolv(monkeypatch, {"test-idp.local": ["127.0.0.1"]})
    allow = (("http", "test-idp.local", 9000, "127.0.0.1/32"),)
    assert ssrf.valider_og_pin("http", "test-idp.local", 9000, allow) \
        == "127.0.0.1"


def test_allowlist_gjelder_kun_eksakt_tuple(monkeypatch):
    _patch_resolv(monkeypatch, {"test-idp.local": ["127.0.0.1"]})
    allow = (("http", "test-idp.local", 9000, "127.0.0.1/32"),)
    # Feil port → ikke dekket av allowlisten → forbudt (loopback).
    with pytest.raises(ssrf.SsrfAvvist):
        ssrf.valider_og_pin("http", "test-idp.local", 9001, allow)


def test_allowlist_krever_at_ip_er_i_cidr(monkeypatch):
    _patch_resolv(monkeypatch, {"test-idp.local": ["10.9.9.9"]})
    allow = (("http", "test-idp.local", 9000, "127.0.0.1/32"),)
    with pytest.raises(ssrf.SsrfAvvist, match="utenfor"):
        ssrf.valider_og_pin("http", "test-idp.local", 9000, allow)


# ---------------------------------------------------------------------------
# Transporten: SNI/Host bevares, URL pekes på pinnet IP (V5)
# ---------------------------------------------------------------------------

def test_transport_pinner_ip_og_bevarer_sni(monkeypatch):
    import httpx
    _patch_resolv(monkeypatch, {"idp.example.com": ["93.184.216.34"]})
    fanget = {}

    def fake_super(self, request):
        fanget["url_host"] = request.url.host
        fanget["sni"] = request.extensions.get("sni_hostname")
        fanget["host_header"] = request.headers.get("Host")
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fake_super)
    t = ssrf.PinnetTransport()
    t.handle_request(httpx.Request("GET", "https://idp.example.com/.well-known"))
    assert fanget["url_host"] == "93.184.216.34", "URL skal peke på pinnet IP"
    assert fanget["sni"] == "idp.example.com", "SNI = original hostname (V5)"
    assert fanget["host_header"] == "idp.example.com", "Host = original"


def test_transport_avviser_privat_for_super_kalles(monkeypatch):
    import httpx
    _patch_resolv(monkeypatch, {"intern.example": ["10.0.0.1"]})
    kalt = []
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request",
                        lambda self, r: kalt.append(1))
    t = ssrf.PinnetTransport()
    with pytest.raises(ssrf.SsrfAvvist):
        t.handle_request(httpx.Request("GET", "https://intern.example/x"))
    assert not kalt, "super().handle_request skal ALDRI nås for forbudt IP"


# ---------------------------------------------------------------------------
# Responsstørrelse-grense (v6 §1)
# ---------------------------------------------------------------------------

def test_les_begrenset_avbryter_over_taket():
    import httpx

    def stor_strom():
        for _ in range(10):
            yield b"a" * 50_000        # 500 KiB totalt

    r = httpx.Response(200, stream=httpx.SyncByteStream() if False else None)
    # Bygg en respons med en strøm.
    r = httpx.Response(200, content=stor_strom())
    with pytest.raises(ssrf.SsrfAvvist, match="over"):
        ssrf.les_begrenset(r, maks=256 * 1024)


def test_les_begrenset_slipper_liten_respons():
    import httpx
    r = httpx.Response(200, content=b'{"issuer":"x"}')
    assert ssrf.les_begrenset(r) == b'{"issuer":"x"}'


def test_ingen_hjemmelaget_jwt_parsing():
    """Klarsignal-port + v6 §3: ingen egen base64-dekoding av JWT-segmenter
    noe sted i api/. Biblioteket eier JWS-validering."""
    import re
    from pathlib import Path
    api = Path(ssrf.__file__).resolve().parent
    mistenkt = []
    for fil in api.glob("*.py"):
        tekst = fil.read_text(encoding="utf-8")
        # Et JWT splittes på '.' i tre segmenter og base64-dekodes. Let etter
        # den kombinasjonen; en enkelt urlsafe_b64decode er ikke nok alene.
        if re.search(r"\.split\(['\"]\.['\"]\)", tekst) and \
                "b64decode" in tekst:
            mistenkt.append(fil.name)
    assert mistenkt == [], f"mulig hjemmelaget JWT-parsing: {mistenkt}"
