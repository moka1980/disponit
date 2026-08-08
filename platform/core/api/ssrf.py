"""IP-pinnet, SSRF-sikker HTTP-transport for OIDC-provider-kall (PR-010
v5 §2 + v6 §1, klarsignal V1/V4/V5).

Hele poenget: et outbound-kall til `discovery_url`/`jwks_uri`/token-
endepunktet styres av providerkonfig, og en kompromittert eller
feilkonfigurert provider kunne ellers nådd loopback, link-local eller
sky-metadata (169.254.169.254). Vernet er et ALLOWLIST-prinsipp (V1):
adressen må være GLOBALT ROUTBAR — det avviser CGNAT, dokumentasjonsnett
og alt annet spesialområde automatisk, uten en liste å vedlikeholde.

DNS-rebinding lukkes ved å PINNE den validerte IP-en til forbindelsen
(v6 §1): vi resolver ALLE A/AAAA, avviser hele requesten hvis ÉN kandidat
er forbudt, kobler til den validerte IP-en, men beholder ORIGINAL hostname
for TLS-SNI og sertifikatvalidering. Biblioteket (Authlib) får DENNE
klienten injisert og gjør aldri sitt eget DNS-oppslag.
"""
from __future__ import annotations

import ipaddress
import re
import socket

import httpx

# --- Konstanter (v6 §1: eksakte, ikke «f.eks.») ----------------------------
MAKS_RESPONS = 256 * 1024
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 5.0
# Redirects = 0 (klarsignal V2). Trengs de senere, er det egen kontrakt med
# revalidering per hopp — ikke en stille default.
MAKS_REDIRECTS = 0

#: Sky-metadata-endepunkter som ikke fanges av de generelle områdene.
METADATA_IP = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
})

#: Hostname-mønster (RFC 1123-labels). En IP-literal treffer aldri dette —
#: den valideres for seg, i kanonisk form, så «0x7f.1»/«2130706433» og andre
#: alternative notasjoner avvises (v6 §1 pkt. 1).
_HOSTNAVN = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


class SsrfAvvist(Exception):
    """Et provider-kall pekte på en adresse egresspolicyen forbyr."""


def _forbudt(ip: ipaddress._BaseAddress) -> bool:
    """En adresse er tillatt KUN hvis den er globalt routbar (allowlist-
    prinsipp, V1). `is_global` er False for loopback, privat, link-local,
    multicast, reservert, unspecified, CGNAT (100.64/10) og
    dokumentasjonsnett — alt i én positiv regel."""
    if ip in METADATA_IP:
        return True
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) må vurderes på den INNBAKTE v4-en,
    # ellers kunne ::ffff:169.254.169.254 sluppet forbi (v6 §1 pkt. 1).
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _forbudt(ip.ipv4_mapped)
    # `is_global` fanger loopback/privat/link-local/CGNAT/dok-nett, men
    # IKKE multicast/reservert/unspecified på alle Python-versjoner — de
    # legges til eksplisitt (v6 §1 pkt. 8).
    return (not ip.is_global or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified)


def normaliser_hostname(host: str) -> str:
    """Kanonisk, små bokstaver, uten etterfølgende punktum. Kaster
    SsrfAvvist på tomt/ugyldig — en hostname vi ikke kan normalisere,
    kobler vi oss ikke til."""
    if not host:
        raise SsrfAvvist("tomt hostname")
    h = host.strip().rstrip(".").lower()
    if not h:
        raise SsrfAvvist("hostname ble tomt etter normalisering")
    return h


def _ip_literal(host: str) -> ipaddress._BaseAddress | None:
    """-> IP hvis `host` er en KANONISK IP-literal, ellers None. En
    ikke-kanonisk form (desimal/oktal/heks/etterfølgende punktum) parser
    ikke her og faller til hostname-veien, som avviser den."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    # Kanonisk sjekk: str(ip) må gi nøyaktig input tilbake. «127.1» eller
    # «0x7f.0.0.1» ville enten feilet over eller ikke round-trippet.
    return ip if str(ip) == host else None


def _match_allowlist(scheme: str, host: str, port: int,
                     allowlist: tuple) -> ipaddress._BaseAddress | None:
    """Staging-unntaket (v6 §1): eksakt (scheme, host, port, IP/CIDR).
    -> den ENE tillatte IP-en hvis (scheme,host,port) matcher og resolver
    innenfor CIDR-en; ellers None (fall til den globale policyen)."""
    for a_scheme, a_host, a_port, a_cidr in allowlist:
        if scheme == a_scheme and host == a_host and port == a_port:
            nett = ipaddress.ip_network(a_cidr, strict=False)
            for ip in _resolv(host, port):
                if ip in nett:
                    return ip
            raise SsrfAvvist(
                f"allowlistet {host}:{port} resolver utenfor {a_cidr}")
    return None


def _resolv(host: str, port: int) -> list[ipaddress._BaseAddress]:
    try:
        info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SsrfAvvist(f"kunne ikke resolve {host}: {e}") from e
    ut = []
    for fam, _, _, _, sockaddr in info:
        ut.append(ipaddress.ip_address(sockaddr[0]))
    if not ut:
        raise SsrfAvvist(f"{host} resolverte til null adresser")
    return ut


def valider_og_pin(scheme: str, host: str, port: int,
                   allowlist: tuple = ()) -> str:
    """Resolver, validerer og velger den pinnede IP-en for ett kall.

    Avviser HELE requesten hvis ÉN kandidat er forbudt (v6 §1 pkt. 3) — en
    hostname som resolver til både offentlig og privat IP slipper aldri
    igjennom. -> den validerte IP-en (str) forbindelsen skal pinnes til.
    """
    if scheme != "https":
        # Kun HTTPS (v5 §2). Staging-allowlisten kan tillate http mot
        # test-IdP-en eksplisitt.
        if not any(scheme == a[0] and host == a[1] and port == a[2]
                   for a in allowlist):
            raise SsrfAvvist(f"ikke-HTTPS-skjema {scheme!r} avvist")

    host = normaliser_hostname(host)

    # Staging-unntak FØRST (test-IdP på loopback er ellers «forbudt»).
    pin = _match_allowlist(scheme, host, port, allowlist)
    if pin is not None:
        return str(pin)

    literal = _ip_literal(host)
    kandidater = [literal] if literal is not None else _resolv(host, port)
    forbudte = [str(ip) for ip in kandidater if _forbudt(ip)]
    if forbudte:
        raise SsrfAvvist(
            f"{host} resolver til forbudt(e) adresse(r): {forbudte}")
    return str(kandidater[0])


class PinnetTransport(httpx.HTTPTransport):
    """httpx-transport som pinner den validerte IP-en pr. request og
    beholder original hostname for SNI + sertifikatvalidering (V5).

    Redirects håndteres av klienten (satt til 0); skulle de aktiveres,
    kjører HVER forespørsel gjennom `handle_request` og revalideres —
    ingen TOCTOU mellom validering og tilkobling (klarsignal-port 4).
    """

    def __init__(self, allowlist: tuple = (), **kw):
        super().__init__(**kw)
        self._allowlist = allowlist

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = url.host
        port = url.port or (443 if url.scheme == "https" else 80)
        pin = valider_og_pin(url.scheme, host, port, self._allowlist)

        # SNI + sertifikatvalidering mot ORIGINAL hostname (V5): httpx/
        # httpcore bruker `sni_hostname`-extension som server_hostname i
        # TLS-handshaket — altså både SNI og verifiseringsnavn.
        request.extensions = dict(request.extensions)
        request.extensions["sni_hostname"] = host
        # Koble til den PINNEDE IP-en, men bevar Host-headeren.
        vert_header = host if port in (80, 443) else f"{host}:{port}"
        request.headers["Host"] = vert_header
        request.url = url.copy_with(host=pin)
        return super().handle_request(request)


def lag_klient(allowlist: tuple = ()) -> httpx.Client:
    """Den pinnede klienten Authlib får injisert. Ingen redirects, korte
    timeouts, og en 256 KiB-grense håndhevet av kalleren via `les_begrenset`
    (httpx har ingen innebygd responsstørrelsesgrense)."""
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT,
                            write=READ_TIMEOUT, pool=CONNECT_TIMEOUT)
    return httpx.Client(
        transport=PinnetTransport(allowlist=allowlist, retries=0),
        timeout=timeout, follow_redirects=False,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=0))


def les_begrenset(respons: httpx.Response, maks: int = MAKS_RESPONS) -> bytes:
    """Leser responskroppen strømmende og AVBRYTER ved `maks` byte
    (v6 §1). En provider som svarer med en uendelig strøm skal ikke kunne
    tømme minnet vårt."""
    data = bytearray()
    for chunk in respons.iter_bytes():
        data.extend(chunk)
        if len(data) > maks:
            respons.close()
            raise SsrfAvvist(f"responskropp over {maks} byte")
    return bytes(data)
