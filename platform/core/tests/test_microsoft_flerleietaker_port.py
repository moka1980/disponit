"""198: et hvilket som helst firma skal kunne logge inn med sin egen M365.

MÅLT MOT MICROSOFT 13/9, FØR NOE BLE SKREVET:

    common        → issuer: https://login.microsoftonline.com/{tenantid}/v2.0
    organizations → issuer: https://login.microsoftonline.com/{tenantid}/v2.0
    consumers     → issuer: https://login.microsoftonline.com/9188040d-…/v2.0
    <guid>        → issuer: https://login.microsoftonline.com/<guid>/v2.0
    <domene>      → issuer: https://login.microsoftonline.com/<guid>/v2.0

De to første svarer med en LITTERAL plassholder, og `oidc.py` sammenlignet
issuer med eksakt likhet to steder. ÉN Microsoft-tenant virket derfor
allerede — eierens egen, med en enkelttenant-URL. Det som ikke virket var
hele poenget: at et firma vi aldri har hørt om kan bruke sin egen M365.

DET FARLIGE STEDET ER IKKE MALEN, DET ER BINDINGEN.
En mal alene godtar `iss` fra hvilken som helst Microsoft-tenant. Kuren
Microsoft dokumenterer — og som porten under måler — er at tenant-GUID-en i
`iss` må være den samme som `tid`-claimet. Faller den bindingen, kan tenant
A utstede et token som utgir seg for tenant B.
"""
import time

import pytest

from .conftest import CORE  # noqa: F401
from api import oidc, ssrf

from joserfc import jwt
from joserfc.jwk import RSAKey, KeySet

_KEY = RSAKey.generate_key(2048, {"kid": "k1", "use": "sig"})
_KEYSET_DOC = KeySet([_KEY]).as_dict(private=False)

MAL = "https://login.microsoftonline.com/{tenantid}/v2.0"
TENANT_A = "11111111-2222-3333-4444-555555555555"
TENANT_B = "99999999-8888-7777-6666-555555555555"
ISS_A = MAL.replace("{tenantid}", TENANT_A)
CLIENT_ID = "disponit-m365"

# `issuer` og `issuer_mal` er BEVISST ULIKE her. Forste utkast lot dem vaere
# samme streng, og da gikk mutasjonen «sammenlign discovery mot
# `provider.issuer` alene» GRONN — testdataene gjorde feilen usynlig. For en
# mal-rad er `issuer` bare identitet og unikhetsnokkel i tabellen; det er
# `issuer_mal` som sammenlignes.
ISSUER_IDENTITET = "https://login.microsoftonline.com/organizations/v2.0"

# Discovery for en flerleietaker svarer med MALEN, ikke med en tenant —
# nøyaktig slik Microsoft gjør det (målt over).
DISCOVERY = {
    "issuer": MAL,
    "authorization_endpoint": "https://login.microsoftonline.com/x/authorize",
    "token_endpoint": "https://login.microsoftonline.com/x/token",
    "jwks_uri": "https://login.microsoftonline.com/x/jwks",
}


def _provider(**over):
    d = dict(provider_id="m365", issuer=ISSUER_IDENTITET,
             discovery_url="https://login.microsoftonline.com/x/.well-known/"
                           "openid-configuration",
             client_id=CLIENT_ID, client_secret="hemmelig",
             tillatte_algoritmer=("RS256",), allowlist=(), issuer_mal=MAL)
    d.update(over)
    return oidc.Provider(**d)


@pytest.fixture(autouse=True)
def _ren_cache():
    oidc.toem_cache()
    yield
    oidc.toem_cache()


def _mock_nett(monkeypatch, discovery=None):
    disc = discovery if discovery is not None else DISCOVERY

    def fake(url, allowlist):
        if url.endswith("openid-configuration"):
            return disc
        if url.endswith("/jwks"):
            return _KEYSET_DOC
        raise AssertionError(f"uventet URL {url}")
    monkeypatch.setattr(oidc, "_hent_json", fake)
    import ipaddress
    monkeypatch.setattr(
        ssrf, "_resolv",
        lambda host, port: [ipaddress.ip_address("93.184.216.34")])


def _id_token(iss=ISS_A, tid=TENANT_A, nonce="n0", **ekstra):
    naa = int(time.time())
    claims = {"iss": iss, "sub": "bruker-abc", "aud": CLIENT_ID,
              "exp": naa + 3600, "iat": naa, "nonce": nonce}
    if tid is not None:
        claims["tid"] = tid
    claims.update(ekstra)
    return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, _KEY)


# ---------------------------------------------------------------------------
# Malen: den skal virke, og den skal ikke kunne utvides
# ---------------------------------------------------------------------------

def test_discovery_med_plassholder_godtas(monkeypatch):
    """Uten dette er hele flerleietaker-veien stengt i første steg.

    MUTASJON SOM FELLER: la `hent_discovery` sammenligne mot `provider.issuer`
    alene igjen.
    """
    _mock_nett(monkeypatch)
    d = oidc.hent_discovery(_provider())
    assert d.issuer == MAL


def test_feil_discovery_issuer_avvises_fortsatt(monkeypatch):
    """POSITIV KONTROLL for porten over: malen løsner ikke på kravet.

    Et discovery-dokument som sier noe ANNET enn malen skal fortsatt avvises
    — ellers hadde porten over vært grønn på at vi sluttet å sjekke.
    """
    _mock_nett(monkeypatch, discovery={**DISCOVERY,
                                       "issuer": "https://ond.example/v2.0"})
    with pytest.raises(oidc.OidcFeil, match="issuer"):
        oidc.hent_discovery(_provider())


def test_malen_er_ikke_et_regex():
    """En mal med regex-metategn skal escapes, ikke tolkes.

    Hadde basen båret et REGEX, ville en tastefeil som `.*` gjort enhver
    utsteder i verden gyldig — stille. Her kan en feilskrevet mal bare gjøre
    mønsteret snevrere.
    """
    m = oidc._issuermonster("https://a.b/.*/{tenantid}/v2.0")
    assert m.match(f"https://a.b/.*/{TENANT_A}/v2.0"), \
        "det LITTERALE punktumet og stjernen skal matche seg selv"
    assert not m.match(f"https://a.b/hvasomhelst/{TENANT_A}/v2.0"), \
        "`.*` fra basen skal ALDRI oppføre seg som et regex"


def test_malen_krever_en_plassholder():
    with pytest.raises(oidc.OidcFeil):
        oidc._issuermonster("https://login.microsoftonline.com/v2.0")


def test_tenantsegmentet_ma_vaere_en_guid():
    """GUID-mønsteret står i KODEN, ikke i raden. En issuer med noe annet i
    tenantposisjonen skal ikke matche."""
    p = _provider()
    with pytest.raises(oidc.OidcFeil):
        oidc._tenant_fra_issuer(
            p, "https://login.microsoftonline.com/../../ond/v2.0")
    assert oidc._tenant_fra_issuer(p, ISS_A) == TENANT_A


# ---------------------------------------------------------------------------
# Bindingen: iss ↔ tid. Det er HER en mal blir farlig uten.
# ---------------------------------------------------------------------------

def test_token_fra_egen_tenant_godtas(monkeypatch):
    _mock_nett(monkeypatch)
    ident = oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                                   _id_token(), "n0", None)
    assert ident.sub == "bruker-abc"


def test_tid_som_peker_paa_en_ANNEN_tenant_avvises(monkeypatch):
    """SELVE VERNET.

    Tokenet sier `iss` = tenant A, men `tid` = tenant B. Uten bindingen ville
    dette autentisert som om det kom fra A.

    MUTASJON SOM FELLER: fjern `tid`-sammenligningen i `_valider_id_token`.
    """
    _mock_nett(monkeypatch)
    with pytest.raises(oidc.OidcFeil, match="tid"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               _id_token(iss=ISS_A, tid=TENANT_B), "n0", None)


def test_token_uten_tid_avvises(monkeypatch):
    """Fravær er ikke samtykke: mangler claimet, er det ingenting å binde mot."""
    _mock_nett(monkeypatch)
    with pytest.raises(oidc.OidcFeil, match="tid"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               _id_token(tid=None), "n0", None)


def test_iss_utenfor_malen_avvises(monkeypatch):
    """En utsteder som ikke er Microsoft i det hele tatt."""
    _mock_nett(monkeypatch)
    ond = "https://ond.example/" + TENANT_A + "/v2.0"
    with pytest.raises(oidc.OidcFeil, match="issuer-mal"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               _id_token(iss=ond), "n0", None)


# ---------------------------------------------------------------------------
# Identiteten
# ---------------------------------------------------------------------------

def test_identiteten_baerer_den_LOSTE_issueren(monkeypatch):
    """`brukeridentitet` er `(issuer, sub)`.

    Ble MALEN stående som issuer, ville alle Microsoft-brukere i verden delt
    én issuer-verdi hos oss, og tenanten vært borte fra identiteten.

    MUTASJON SOM FELLER: sett `issuer=provider.issuer` tilbake i `Identitet`.
    """
    _mock_nett(monkeypatch)
    ident = oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                                   _id_token(), "n0", None)
    assert ident.issuer == ISS_A, "issueren skal bære den FAKTISKE tenanten"
    assert "{tenantid}" not in ident.issuer


def test_enkelttenant_er_uendret(monkeypatch):
    """REGRESJONSVERN FOR GOOGLE OG FOR EIERENS EGEN M365.

    Uten `issuer_mal` skal alt være som før: eksakt likhet, ingen `tid`, og
    issueren fra raden. Google i prod går denne veien.
    """
    fast = "https://accounts.google.example"
    disc = {"issuer": fast,
            "authorization_endpoint": f"{fast}/auth",
            "token_endpoint": f"{fast}/token",
            "jwks_uri": f"{fast}/jwks"}

    def fake(url, allowlist):
        return disc if url.endswith("configuration") else _KEYSET_DOC
    monkeypatch.setattr(oidc, "_hent_json", fake)
    import ipaddress
    monkeypatch.setattr(
        ssrf, "_resolv",
        lambda host, port: [ipaddress.ip_address("93.184.216.34")])

    p = _provider(issuer=fast, issuer_mal=None,
                  discovery_url=f"{fast}/.well-known/openid-configuration")
    assert oidc.hent_discovery(p).issuer == fast
    # INGEN `tid` i tokenet — en enkelttenant-IdP sender ikke noe slikt, og
    # skal ikke avkreves det.
    ident = oidc._valider_id_token(p, oidc.Discovery(**disc),
                                   _id_token(iss=fast, tid=None), "n0", None)
    assert ident.issuer == fast
