"""M-6 PR-B: kilderegistrering med M365-OAuth — portene.

Dommene 31/8 er kontrakten denne fila måler:

  1. M365 først, og KUN lesende scope: `Mail.Read offline_access`. Ett
     scope mer er en KONTRAKTSENDRING, ikke en detalj — derfor står
     scope-strengen som en egen port, sammen med fraværet av enhver
     sendevei i modulen.
  2. v1 sender aldri. Ingen `Mail.Send`, ingen `sendMail`, ingen
     Graph-write noe sted i kildeveien.

Portene (planens §4 for PR-B):

  * State-kapabiliteten er MAC-et, ENGANGS og kortlivet — forfalsket,
    replayet og utløpt state avvises alle tre, og ingen av dem skriver
    en kilde.
  * Uten M365-konfig svarer /start den ÆRLIGE koden og etterlater
    INGEN halv flyt: ingen state, ingen bindingcookie, ingen
    idempotensrad.
  * Refresh-tokenet finnes aldri i klartekst: raden bærer ciphertext,
    og INTET HTTP-svar i flyten bærer tokenet.
  * Callback med ugyldig state skriver aldri en kilde, og gjengir aldri
    URL-parametere.
  * All outbound HTTP går over ssrf-transporten (statisk + dynamisk
    port) — Microsoft nås aldri med en naken httpx-klient.
  * PKCE holder hele veien: verifieren callbacken sender er den
    challenge-en /start publiserte.
  * Deaktivering er enveis `aktiv` → `deaktivert`.

Microsoft er MOCKET (kodevekslingen injiseres, DNS pinnes) — ingen
ekte OAuth i test. Flatens axe-port bor i `ui/test/epost.test.js`.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst
from .test_outbox_bestilling import _adminsesjon

ROT = Path(__file__).resolve().parents[3]
KILDEMODUL = ROT / "platform" / "core" / "api" / "epost_kilde.py"

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

M365_ID = "m6b-klient-id"
M365_SECRET = "m6b-klient-hemmelighet"
#: Refresh-tokenet Microsoft «returnerer». Unik per kjøring, så en
#: fraværstest ikke kan gå grønn på en gammel streng.
REFRESH = "m365-refresh-" + secrets.token_hex(12)


# ---------------------------------------------------------------------------
# Rigg
# ---------------------------------------------------------------------------

@pytest.fixture()
def m365(monkeypatch):
    """Konfigurert M365 + pinnet DNS. `_resolv` mockes som i OIDC-flyt-
    testene: egresspolicyen skal MÅLES, ikke omgås, men den skal ikke
    kreve et ekte navneoppslag i CI."""
    from api import ssrf
    monkeypatch.setenv("DISPONIT_M365_CLIENT_ID", M365_ID)
    monkeypatch.setenv("DISPONIT_M365_CLIENT_SECRET", M365_SECRET)
    monkeypatch.delenv("DISPONIT_M365_TENANT", raising=False)
    monkeypatch.delenv("DISPONIT_M365_ALLOWLIST", raising=False)
    monkeypatch.setattr(
        ssrf, "_resolv",
        lambda h, p: [ipaddress.ip_address("20.190.190.1")])
    return monkeypatch


def _postboks() -> str:
    return f"m6b-{secrets.token_hex(5)}@example.org"


def _start(klient_, cookie, csrf, postboks, nokkel=None):
    from api import sesjon as sesjonmodul
    return klient_.post(
        "/v1/epost/kilder/start", json={"postboks": postboks},
        headers={"X-Disponit-CSRF": csrf,
                 "Idempotency-Key": nokkel or ("m6b-" + secrets.token_hex(8))},
        cookies={sesjonmodul.C_SESJON: cookie})


def _binding(respons) -> str | None:
    from api.epost_kilde import C_BINDING
    for c in respons.headers.get_list("set-cookie"):
        if f"{C_BINDING}=" in c:
            verdi = c.split(f"{C_BINDING}=")[1].split(";")[0]
            return verdi or None
    return None


def _autorisasjonsparametre(respons) -> dict:
    """Query-parametrene i authorize-URL-en, ett nivå ut av lista."""
    q = parse_qs(urlsplit(respons.json()["autorisasjonsurl"]).query)
    return {k: v[0] for k, v in q.items()}


def _callback(klient_, state, binding):
    from api.epost_kilde import C_BINDING
    cookies = {C_BINDING: binding} if binding else {}
    return klient_.get("/v1/epost/kilder/callback",
                       params={"code": "microsoft-kode", "state": state},
                       cookies=cookies, follow_redirects=False)


def _mock_veksling(monkeypatch, fanget=None, svar=None):
    """Microsofts token-endepunkt. Samme snitt som OIDC-testenes
    `veksle_og_valider`: funksjonen byttes ut, transporten røres ikke."""
    from api import epost_kilde as kildemodul

    def veksle(konfig, code, verifier, redirect_uri):
        if fanget is not None:
            fanget.update({"code": code, "verifier": verifier,
                           "redirect_uri": redirect_uri,
                           "client_id": konfig.client_id})
        return svar if svar is not None else {"refresh_token": REFRESH,
                                              "access_token": "kortlivet",
                                              "expires_in": 3600}
    monkeypatch.setattr(kildemodul, "_veksle_kode", veksle)


def _koble_til(klient_, monkeypatch, cookie, csrf, postboks, fanget=None):
    """Hele den grønne flyten: /start → Microsoft → /callback."""
    r = _start(klient_, cookie, csrf, postboks)
    assert r.status_code == 200, r.text
    param = _autorisasjonsparametre(r)
    _mock_veksling(monkeypatch, fanget=fanget)
    cb = _callback(klient_, param["state"], _binding(r))
    return r, param, cb


def _konvolutt(state: str) -> dict:
    """State-tokenets konvolutt, dekodet. `bygg_state` pakker den som ÉN
    base64url-blokk — ingen punktsegmenter å splitte, og dermed ingenting
    som ligner et JWT."""
    return json.loads(base64.urlsafe_b64decode(
        state + "=" * (-len(state) % 4)))


def _kilderad(migrator_, postboks):
    _sett_kontekst(migrator_, TENANT)
    rad = migrator_.execute(
        "SELECT kilde_id, status, auth_kryptert, nonce, key_id,"
        " epost_kilde::text FROM epost_kilde"
        " WHERE tenant=%s AND postboks=%s", (TENANT, postboks)).fetchone()
    migrator_.rollback()
    return rad


# ---------------------------------------------------------------------------
# Port: dommen om scope — v1 er lesende, og sendeveien er urepresenterbar
# ---------------------------------------------------------------------------

def test_scopet_er_kun_lesende_og_modulen_har_ingen_sendevei():
    """Dommen 31/8 pkt. 1–2, målt på kilden: nøyaktig `Mail.Read` +
    `offline_access`, og ingen skrive-/sendeverb i hele kildeveien. En
    scope-utvidelse er en kontraktsendring — den skal FELLE en test,
    ikke gli gjennom som en strengendring."""
    from api.epost_kilde import M365_SCOPE
    assert M365_SCOPE.split() == [
        "https://graph.microsoft.com/Mail.Read", "offline_access"], \
        "scopet er dommens, ikke utviklerens"
    kilde = KILDEMODUL.read_text(encoding="utf-8")
    for forbudt in ("Mail.Send", "Mail.ReadWrite", "sendMail",
                    "Mail.ReadBasic", "MailboxSettings"):
        assert forbudt not in kilde, \
            f"{forbudt} gir kildeveien en evne v1 ikke skal ha"


def test_staten_er_en_konvolutt_ikke_et_jwt():
    """Kapabiliteten er husets egen, og den skal ikke se ut som et JWT:
    ÉN base64url-konvolutt med payload, key_id og mac — ingen
    punktsegmenter noen kunne fristes til å parse selv, og ingenting som
    inviterer en leser til å tro at et JWS-bibliotek har vært involvert.
    Klarsignal-porten i `test_ssrf` måler den samme regelen statisk."""
    kilde = KILDEMODUL.read_text(encoding="utf-8")
    assert ".split(\".\")" not in kilde and ".split('.')" not in kilde, \
        "punktsplitt + base64 er formen hjemmelaget JWS-parsing har"


def test_all_outbound_gaar_over_ssrf_transporten():
    """Statisk port: Microsoft nås ALDRI med en naken klient. Modulen
    importerer ingen HTTP-bibliotek selv — den eneste veien ut er
    `ssrf.lag_klient`, og hvert endepunkt valideres av `valider_og_pin`
    før det brukes."""
    kilde = KILDEMODUL.read_text(encoding="utf-8")
    for forbudt in ("import httpx", "import requests", "urllib.request",
                    "http.client", "urlopen"):
        assert forbudt not in kilde, \
            f"{forbudt} omgår den IP-pinnede transporten"
    assert kilde.count("ssrf.lag_klient(") == kilde.count(".post("), \
        "et outbound-kall uten en ssrf-klient foran seg"
    assert "ssrf.valider_og_pin(" in kilde


def test_veksleveien_bruker_ssrf_klienten_og_arver_egresspolicyen(m365):
    """Dynamisk motstykke: `_veksle_kode` bygger klienten sin med
    `ssrf.lag_klient` og sender allowlisten videre — og et endepunkt
    egresspolicyen avviser blir en KildeFeil FØR noe kall skjer."""
    from api import epost_kilde as kildemodul
    from api import ssrf

    konfig = kildemodul.hent_konfig()
    assert konfig is not None
    sett = {}

    class FalskRespons:
        status_code = 200
        content = b'{"refresh_token":"r"}'

        def read(self):
            return self.content

    class FalskKlient:
        def post(self, url, data=None):
            sett["url"] = url
            sett["data"] = data
            return FalskRespons()

        def close(self):
            sett["lukket"] = True

    m365.setattr(ssrf, "lag_klient",
                 lambda allowlist=(): sett.setdefault("klient", FalskKlient()))
    m365.setattr(ssrf, "les_begrenset", lambda r, maks=None: r.content)
    ut = kildemodul._veksle_kode(konfig, "kode", "verifier", "https://r/cb")
    assert ut == {"refresh_token": "r"}
    assert sett["url"] == konfig.token_endepunkt
    assert sett["data"]["grant_type"] == "authorization_code"
    assert sett["lukket"] is True, "klienten skal lukkes uansett utgang"

    # Egresspolicyen er en PORT, ikke en pynt: avvises verten, kastes
    # KildeFeil før klienten i det hele tatt bygges.
    m365.setattr(ssrf, "valider_og_pin",
                 lambda *a, **k: (_ for _ in ()).throw(
                     ssrf.SsrfAvvist("nektet")))
    with pytest.raises(kildemodul.KildeFeil):
        kildemodul._veksle_kode(konfig, "kode", "verifier", "https://r/cb")


# ---------------------------------------------------------------------------
# Port: /start
# ---------------------------------------------------------------------------

@pg
def test_start_bygger_authorize_url_med_state_pkce_og_binding(klient, migrator,
                                                              m365):
    """/start utsteder authorize-URL-en SERVEREN eier: rett endepunkt,
    dommens scope, PKCE S256, en MAC-et state og en HttpOnly/Secure
    bindingcookie. Klienthemmeligheten reiser ALDRI med (den hører til
    kodevekslingen, server-til-server)."""
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    r = _start(klient, cookie, csrf, postboks)
    assert r.status_code == 200, r.text
    url = r.json()["autorisasjonsurl"]
    delt = urlsplit(url)
    assert delt.scheme == "https"
    assert delt.hostname == "login.microsoftonline.com"
    assert delt.path == "/organizations/oauth2/v2.0/authorize"
    param = _autorisasjonsparametre(r)
    assert param["response_type"] == "code"
    assert param["client_id"] == M365_ID
    assert param["scope"] == ("https://graph.microsoft.com/Mail.Read"
                             " offline_access")
    assert param["code_challenge_method"] == "S256"
    assert param["code_challenge"]
    assert param["redirect_uri"].endswith("/v1/epost/kilder/callback")
    assert M365_SECRET not in url, \
        "klienthemmeligheten skal aldri i en browser-URL"
    binding = _binding(r)
    assert binding, "bindingcookien mangler"
    rå = [c for c in r.headers.get_list("set-cookie") if binding in c][0]
    assert "HttpOnly" in rå and "Secure" in rå and "SameSite=Lax" in rå
    # /start alene skriver INGEN kilde — raden fødes i callbacken.
    assert _kilderad(migrator, postboks) is None


@pg
@dekker("m365_ikke_konfigurert")
def test_uten_m365_konfig_er_svaret_aerlig_og_flyten_aldri_halv(klient,
                                                                migrator,
                                                                monkeypatch):
    """M365 er VALGFRITT konfigurert. Mangler credentialene, sier
    endepunktet det RETT UT — og etterlater ingenting: ingen state
    ingen callback kan fullføre, ingen bindingcookie, og ingen
    idempotensrad som blokkerer et senere ekte forsøk."""
    monkeypatch.delenv("DISPONIT_M365_CLIENT_ID", raising=False)
    monkeypatch.delenv("DISPONIT_M365_CLIENT_SECRET", raising=False)
    cookie, csrf = _adminsesjon()
    nokkel = "m6b-" + secrets.token_hex(8)
    r = _start(klient, cookie, csrf, _postboks(), nokkel)
    assert r.status_code == 503, r.text
    assert r.json()["feil"] == "m365_ikke_konfigurert"
    assert "autorisasjonsurl" not in r.json()
    assert _binding(r) is None, "en halv flyt satte likevel bindingen"
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM idempotens"
                         " WHERE tenant=%s AND nokkel=%s",
                         (TENANT, nokkel)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "nøkkelen ble brent på en flyt som aldri fantes"


@pg
def test_ugyldig_tenantsegment_er_ukonfigurert_ikke_en_url(klient, m365):
    """Tenantsegmentet er en URL-PATH-komponent fra miljøet. Bærer den
    skilletegn, er konfigurasjonen FEIL — og fail-closed er den samme
    ærlige koden, aldri en authorize-URL med et injisert ledd."""
    m365.setenv("DISPONIT_M365_TENANT", "acme/../../evil")
    cookie, csrf = _adminsesjon()
    r = _start(klient, cookie, csrf, _postboks())
    assert r.status_code == 503
    assert r.json()["feil"] == "m365_ikke_konfigurert"


@pg
def test_start_replayer_samme_url_og_samme_binding(klient, m365):
    """Idempotensen (003-lageret): samme nøkkel + samme postboks gir
    NØYAKTIG samme authorize-URL og samme bindingcookie — et tapt svar
    og et nytt klikk skal ikke utstede state nummer to. Samme nøkkel
    med en ANNEN postboks er en annen bestilling og gir konflikt."""
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    nokkel = "m6b-" + secrets.token_hex(8)
    r1 = _start(klient, cookie, csrf, postboks, nokkel)
    r2 = _start(klient, cookie, csrf, postboks, nokkel)
    assert r1.status_code == 200 and r2.status_code == 200, r2.text
    assert r1.json() == r2.json()
    assert _binding(r1) == _binding(r2)
    r3 = _start(klient, cookie, csrf, _postboks(), nokkel)
    assert r3.status_code == 409
    assert r3.json()["feil"] == "idempotenskonflikt"


@pg
@dekker("idempotensnokkel_reservert")
def test_kalleren_kan_ikke_skrive_i_callbackens_nokkelrom(klient, m365):
    """`m365kilde:` er RESERVERT (kjerne.RESERVERTE_NOKKELROM): en
    planta rad i callbackens spor ville latt en kaller «forbruke» en
    annens state før den ble brukt."""
    from api import kjerne
    assert kjerne.er_reservert_nokkel("m365kilde:noe")
    cookie, csrf = _adminsesjon()
    r = _start(klient, cookie, csrf, _postboks(), "m365kilde:kapret")
    assert r.status_code == 400
    assert r.json()["feil"] == "idempotensnokkel_reservert"


@pg
def test_start_krever_administrerscopet_og_avviser_feilformet_postboks(
        klient, m365):
    """Lesescopet ser kildene; det KOBLER dem ikke til. Og postboksen
    valideres server-side — et felt som ikke er en adresse skal aldri
    nå Microsoft."""
    leser, leser_csrf = _adminsesjon(roller="leser")
    r = _start(klient, leser, leser_csrf, _postboks())
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "scope_mangler"
    cookie, csrf = _adminsesjon()
    for ugyldig in ("", "ikke-en-adresse", "a@b", "to@adr esser@x.no",
                    "x@" + "y" * 300 + ".no"):
        r = _start(klient, cookie, csrf, ugyldig)
        assert r.status_code == 400, (ugyldig, r.text)
        assert r.json()["feil"] == "request_feilformet"


# ---------------------------------------------------------------------------
# Port: /callback — den grønne veien
# ---------------------------------------------------------------------------

@pg
def test_callback_skriver_kilden_og_refresh_tokenet_er_ciphertext(
        klient, migrator, m365):
    """Den grønne veien, og credential-porten i den: raden fødes med
    status `aktiv`, refresh-tokenet ligger som ciphertext bak tenant-
    DEK-en, og INTET HTTP-svar i hele flyten bærer tokenet. Positivt:
    DEK-en åpner det igjen — en fraværstest uten den går grønn på
    søppel."""
    from db import kryptering
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    fanget: dict = {}
    r, param, cb = _koble_til(klient, m365, cookie, csrf, postboks, fanget)
    assert cb.status_code == 303, cb.text
    assert cb.headers["location"] == "/#/epost"
    # Bindingen ryddes: cookien overlever ikke sin ene runde.
    assert _binding(cb) is None

    rad = _kilderad(migrator, postboks)
    assert rad is not None, "callbacken skrev ingen kilde"
    kilde_id, status, ct, nonce, key_id, som_tekst = rad
    assert status == "aktiv"
    assert REFRESH not in som_tekst, \
        "refresh-tokenet ligger i klartekst i en kolonne"
    _sett_kontekst(migrator, TENANT)
    dek = kryptering.hent_dek(migrator, TENANT, key_id)
    migrator.rollback()
    assert kryptering.dekrypter(dek, ct, nonce, TENANT, key_id) == \
        {"refresh_token": REFRESH}

    # Ingen av svarene i flyten bærer tokenet — heller ikke lista.
    liste = klient.get("/v1/epost/kilder",
                       cookies={"__Host-disponit_sesjon": cookie})
    assert liste.status_code == 200, liste.text
    for svar in (r.text, cb.text, liste.text):
        assert REFRESH not in svar
        assert M365_SECRET not in svar
    poster = [k for k in liste.json()["kilder"]
              if k["postboks"] == postboks]
    assert len(poster) == 1
    assert set(poster[0]) == {"kilde_id", "leverandor", "postboks", "status",
                              "sist_hentet_ts", "opprettet"}, \
        "leseflaten har fått en kolonne den ikke skal ha"
    assert poster[0]["kilde_id"] == str(kilde_id)


@pg
def test_pkce_verifieren_matcher_challengen_start_publiserte(klient, m365):
    """PKCE holder HELE veien: verifieren callbacken sender til
    Microsoft er den `code_challenge` browseren fikk se — den reiste
    DEK-kryptert i statens payload, ikke i klartekst."""
    cookie, csrf = _adminsesjon()
    fanget: dict = {}
    r, param, cb = _koble_til(klient, m365, cookie, csrf, _postboks(),
                              fanget)
    assert cb.status_code == 303, cb.text
    verifier = fanget["verifier"]
    forventet = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert param["code_challenge"] == forventet
    assert fanget["redirect_uri"] == param["redirect_uri"]
    assert fanget["client_id"] == M365_ID
    # Verifieren står ALDRI lesbart i det browseren fikk.
    assert verifier not in r.text
    assert verifier not in param["state"]


# ---------------------------------------------------------------------------
# Port: state-kapabiliteten — MAC, TTL, engangs
# ---------------------------------------------------------------------------

@pg
@dekker("m365_tilkobling_feilet")
def test_forfalsket_state_avvises_uten_kildeskriving(klient, migrator, m365,
                                                     monkeypatch):
    """MAC-en er hele forsvaret: en payload som er endret ETTER
    signeringen — her byttet postboks — verifiserer ikke, og
    callbacken avviser FØR kodeveksling og FØR enhver kildeskriving.
    Svaret er generisk og gjengir aldri URL-parametere."""
    from api import epost_kilde as kildemodul
    cookie, csrf = _adminsesjon()
    egen, kapret = _postboks(), _postboks()
    r = _start(klient, cookie, csrf, egen)
    param = _autorisasjonsparametre(r)
    konvolutt = _konvolutt(param["state"])
    konvolutt["payload"]["postboks"] = kapret     # MAC-en står urørt
    forfalsket = base64.urlsafe_b64encode(
        json.dumps(konvolutt, ensure_ascii=False,
                   separators=(",", ":")).encode()).decode().rstrip("=")

    def aldri(*a, **k):
        raise AssertionError("kodeveksling på en forfalsket state")
    monkeypatch.setattr(kildemodul, "_veksle_kode", aldri)

    cb = _callback(klient, forfalsket, _binding(r))
    assert cb.status_code == 400, cb.text
    assert cb.json() == {"feil": "m365_tilkobling_feilet"}
    assert kapret not in cb.text and "microsoft-kode" not in cb.text
    assert _kilderad(migrator, egen) is None
    assert _kilderad(migrator, kapret) is None


@pg
def test_utlopt_state_avvises_uten_kildeskriving(klient, migrator, m365,
                                                 monkeypatch):
    """TTL-en er OIDC-flytens (10 min): en authorize-runde tar sekunder.
    En state som er SIGNERT av huset, men utløpt, er like død som en
    forfalsket — signaturen beviser opphav, ikke ferskhet."""
    from api import epost_kilde as kildemodul
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    r = _start(klient, cookie, csrf, postboks)
    param = _autorisasjonsparametre(r)
    payload = _konvolutt(param["state"])["payload"]
    payload["utloper"] = int(time.time()) - 1
    # SIGNERT PÅ NYTT av husets egen nøkkel: porten måler TTL-en, ikke
    # MAC-en (den har sin egen test over).
    utlopt = kildemodul.bygg_state(klient.app.tjeneste.mac_register, payload)

    def aldri(*a, **k):
        raise AssertionError("kodeveksling på en utløpt state")
    monkeypatch.setattr(kildemodul, "_veksle_kode", aldri)

    cb = _callback(klient, utlopt, _binding(r))
    assert cb.status_code == 400, cb.text
    assert cb.json() == {"feil": "m365_tilkobling_feilet"}
    assert _kilderad(migrator, postboks) is None


@pg
def test_brutt_browserbinding_avvises_uten_kildeskriving(klient, migrator,
                                                         m365, monkeypatch):
    """Bindingcookien (v4 §1) knytter staten til BROWSEREN som startet
    flyten. En gyldig, fersk state uten — eller med en fremmed —
    binding er en påtvunget tilkobling, og avvises."""
    from api import epost_kilde as kildemodul
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    r = _start(klient, cookie, csrf, postboks)
    state = _autorisasjonsparametre(r)["state"]

    def aldri(*a, **k):
        raise AssertionError("kodeveksling på en brutt binding")
    monkeypatch.setattr(kildemodul, "_veksle_kode", aldri)

    for binding in (None, "en-helt-annen-binding",
                    kildemodul.binding_for(klient.app.tjeneste.mac_register,
                                           secrets.token_hex(16))):
        cb = _callback(klient, state, binding)
        assert cb.status_code == 400, (binding, cb.text)
        assert cb.json() == {"feil": "m365_tilkobling_feilet"}
        assert _kilderad(migrator, postboks) is None


@pg
def test_callback_uten_code_eller_state_avvises(klient, m365):
    """Fravær behandles som avvisning, ikke som et halvt forsøk."""
    cookie, csrf = _adminsesjon()
    r = _start(klient, cookie, csrf, _postboks())
    state, binding = _autorisasjonsparametre(r)["state"], _binding(r)
    from api.epost_kilde import C_BINDING
    for params in ({"state": state}, {"code": "k"}, {}):
        cb = klient.get("/v1/epost/kilder/callback", params=params,
                        cookies={C_BINDING: binding},
                        follow_redirects=False)
        assert cb.status_code == 400, (params, cb.text)
        assert cb.json() == {"feil": "m365_tilkobling_feilet"}


@pg
def test_staten_er_engangs_og_et_replay_skriver_aldri_paa_nytt(
        klient, migrator, m365, monkeypatch):
    """ENGANGS, målt der det betyr noe: etter en fullført tilkobling og
    en DEAKTIVERING replayes nøyaktig samme state + binding. Går
    replayet gjennom, ville kilden blitt REAKTIVERT uten nytt samtykke
    — så statusen etterpå er porten, ikke bare statuskoden."""
    from api import epost_kilde as kildemodul
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    r, param, cb = _koble_til(klient, m365, cookie, csrf, postboks)
    assert cb.status_code == 303, cb.text
    kilde_id = _kilderad(migrator, postboks)[0]

    d = klient.post(f"/v1/epost/kilder/{kilde_id}/deaktiver",
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={"__Host-disponit_sesjon": cookie})
    assert d.status_code == 200, d.text
    assert _kilderad(migrator, postboks)[1] == "deaktivert"

    def aldri(*a, **k):
        raise AssertionError("kodeveksling på en konsumert state")
    monkeypatch.setattr(kildemodul, "_veksle_kode", aldri)

    replay = _callback(klient, param["state"], _binding(r))
    assert replay.status_code == 400, replay.text
    assert replay.json() == {"feil": "m365_tilkobling_feilet"}
    assert _kilderad(migrator, postboks)[1] == "deaktivert", \
        "et replay reaktiverte kilden uten nytt samtykke"


@pg
def test_konsumet_committes_foer_nettverkskallet(klient, migrator, m365,
                                                 monkeypatch):
    """v4 §3: staten brukes OPP før kodevekslingen. Feiler vekslingen,
    er flyten likevel forbrukt — administratoren starter forfra, og en
    angriper får ikke prøve den samme staten om igjen mot et endepunkt
    som svarer ulikt fra gang til gang."""
    from api import epost_kilde as kildemodul
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    r = _start(klient, cookie, csrf, postboks)
    param = _autorisasjonsparametre(r)

    def feiler(*a, **k):
        raise kildemodul.KildeFeil("Microsoft svarte 400")
    monkeypatch.setattr(kildemodul, "_veksle_kode", feiler)
    cb = _callback(klient, param["state"], _binding(r))
    assert cb.status_code == 400
    assert _kilderad(migrator, postboks) is None

    # Staten er BRUKT OPP, ikke bare mislykket: en ny runde med et
    # fungerende Microsoft endrer ingenting.
    _mock_veksling(monkeypatch)
    igjen = _callback(klient, param["state"], _binding(r))
    assert igjen.status_code == 400
    assert _kilderad(migrator, postboks) is None


@pg
def test_tokensvar_uten_refresh_token_skriver_ingen_kilde(klient, migrator,
                                                          m365, monkeypatch):
    """Uten refresh-token finnes ingen kilde å hente fra: en rad med
    tom credential ville vært en tilkobling som ser levende ut og aldri
    kan brukes."""
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    r = _start(klient, cookie, csrf, postboks)
    param = _autorisasjonsparametre(r)
    _mock_veksling(monkeypatch, svar={"access_token": "bare-access"})
    cb = _callback(klient, param["state"], _binding(r))
    assert cb.status_code == 400
    assert _kilderad(migrator, postboks) is None


# ---------------------------------------------------------------------------
# Port: rekobling og deaktivering
# ---------------------------------------------------------------------------

@pg
def test_rekobling_roterer_credentialet_uten_a_lage_en_ny_kilde(
        klient, migrator, m365, monkeypatch):
    """`kilde_en_per_postboks` er idempotensens fundament (088 §2): en
    ny samtykkerunde mot SAMME boks roterer credential-trioen og setter
    `aktiv` igjen — den lager aldri kilde nummer to, og den beholder
    kilde_id-en meldingene henger på."""
    from db import kryptering
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    _koble_til(klient, m365, cookie, csrf, postboks)
    forst = _kilderad(migrator, postboks)
    assert forst is not None

    nytt = "m365-refresh-rotert-" + secrets.token_hex(8)
    r = _start(klient, cookie, csrf, postboks)
    param = _autorisasjonsparametre(r)
    _mock_veksling(monkeypatch, svar={"refresh_token": nytt})
    cb = _callback(klient, param["state"], _binding(r))
    assert cb.status_code == 303, cb.text

    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM epost_kilde"
                         " WHERE tenant=%s AND postboks=%s",
                         (TENANT, postboks)).fetchone()[0]
    migrator.rollback()
    assert n == 1, "rekobling laget en kilde til på samme postboks"
    etter = _kilderad(migrator, postboks)
    assert etter[0] == forst[0], "kilde_id-en er identiteten og skal bestå"
    assert etter[1] == "aktiv"
    assert bytes(etter[2]) != bytes(forst[2]), "credentialet ble ikke rotert"
    _sett_kontekst(migrator, TENANT)
    dek = kryptering.hent_dek(migrator, TENANT, etter[4])
    migrator.rollback()
    assert kryptering.dekrypter(dek, etter[2], etter[3], TENANT,
                                etter[4]) == {"refresh_token": nytt}


@pg
def test_deaktivering_er_enveis_idempotent_og_scopegated(klient, migrator,
                                                         m365):
    """`aktiv` → `deaktivert`, én vei. Et gjensyn på en alt deaktivert
    kilde er idempotent (samme svar, ingen 409-støy), en ukjent id er
    404 uten oppslagsverk, og lesescopet får ikke deaktivere."""
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    _koble_til(klient, m365, cookie, csrf, postboks)
    kilde_id = _kilderad(migrator, postboks)[0]

    leser, leser_csrf = _adminsesjon(roller="leser")
    nektet = klient.post(f"/v1/epost/kilder/{kilde_id}/deaktiver",
                         headers={"X-Disponit-CSRF": leser_csrf},
                         cookies={"__Host-disponit_sesjon": leser})
    assert nektet.status_code == 403, nektet.text
    assert _kilderad(migrator, postboks)[1] == "aktiv"

    d = klient.post(f"/v1/epost/kilder/{kilde_id}/deaktiver",
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={"__Host-disponit_sesjon": cookie})
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "deaktivert"
    assert _kilderad(migrator, postboks)[1] == "deaktivert"

    igjen = klient.post(f"/v1/epost/kilder/{kilde_id}/deaktiver",
                        headers={"X-Disponit-CSRF": csrf},
                        cookies={"__Host-disponit_sesjon": cookie})
    assert igjen.status_code == 200, igjen.text
    assert igjen.json()["status"] == "deaktivert"

    ukjent = klient.post(
        "/v1/epost/kilder/00000000-0000-4000-8000-000000000000/deaktiver",
        headers={"X-Disponit-CSRF": csrf},
        cookies={"__Host-disponit_sesjon": cookie})
    assert ukjent.status_code == 404
    assert ukjent.json()["feil"] == "ikke_funnet"


@pg
def test_mutasjonene_krever_csrf(klient, m365):
    """Browsermutasjonene er dobbel-innsending (v2 §8): uten
    X-Disponit-CSRF er de ingenting, uansett gyldig sesjonscookie."""
    cookie, _csrf = _adminsesjon()
    r = klient.post("/v1/epost/kilder/start",
                    json={"postboks": _postboks()},
                    headers={"Idempotency-Key": "m6b-" + secrets.token_hex(8)},
                    cookies={"__Host-disponit_sesjon": cookie})
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "csrf_ugyldig"


# ---------------------------------------------------------------------------
# Port: token-utleveringen til arbeideren (PR-C-forberedelsen)
# ---------------------------------------------------------------------------

@pg
def test_utleveringen_er_en_ren_funksjon_uten_endepunkt(klient, migrator,
                                                        m365, monkeypatch):
    """`hent_access_token` pakker ut credentialet på KALLERENS kobling
    og veksler til et kortlivet access-token. Den har med vilje INGEN
    rute: web-API-rollen er nektet SELECT på credential-trioen, så
    utleveringsdøren hører til arbeiderrollen — og claim-bindingen den
    skal bindes til finnes først i PR-C. Her måles kontrakten, ikke en
    HTTP-flate: en deaktivert kilde utleverer aldri."""
    from api import epost_kilde as kildemodul
    from api.app import RUTESCOPE
    assert not any(sti.startswith("/v1/epost/kilder")
                   and sti.endswith(("token", "access"))
                   for _m, sti in RUTESCOPE), \
        "utleveringen fikk en rute før claim-bindingen finnes"

    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    _koble_til(klient, m365, cookie, csrf, postboks)
    kilde_id = _kilderad(migrator, postboks)[0]
    konfig = kildemodul.hent_konfig()
    sett: dict = {}

    def veksler(k, refresh):
        sett["refresh"] = refresh
        return {"access_token": "kortlivet-access", "expires_in": 3600}

    _sett_kontekst(migrator, TENANT)
    access = kildemodul.hent_access_token(migrator, TENANT, kilde_id,
                                          konfig=konfig, veksler=veksler)
    migrator.rollback()
    assert access == "kortlivet-access"
    assert sett["refresh"] == REFRESH, \
        "utleveringen sendte noe annet enn kildens eget refresh-token"

    d = klient.post(f"/v1/epost/kilder/{kilde_id}/deaktiver",
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={"__Host-disponit_sesjon": cookie})
    assert d.status_code == 200
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(kildemodul.KildeFeil):
        kildemodul.hent_access_token(migrator, TENANT, kilde_id,
                                     konfig=konfig, veksler=veksler)
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port: redirect-URI-en
# ---------------------------------------------------------------------------

def test_callbackstien_er_den_samme_som_ruten():
    """Én streng, ikke to: `CALLBACKSTI` er både konfigvalideringens
    fasit og fallbackens hale, og den MÅ være ruten som faktisk er
    registrert — ellers peker samtykket et sted appen ikke svarer."""
    from api.app import RUTESCOPE
    from api.epost_kilde import CALLBACKSTI
    assert ("GET", CALLBACKSTI) in RUTESCOPE
    assert RUTESCOPE[("GET", CALLBACKSTI)] is None, \
        "callbacken er en navigasjon fra Microsoft, ikke en scope-gate"


@pg
def test_registrert_redirect_uri_vinner_over_forespoerselens_host(klient,
                                                                  m365):
    """Redirect-URI-en MÅ stemme byte for byte med app-registreringen i
    Azure, så den skal kunne PINNES i miljøet. Er den satt, brukes den
    — og da er ikke en forespørselsheader lenger den eneste kilden til
    adressen eier sendes tilbake til."""
    m365.setenv("DISPONIT_M365_REDIRECT_URI",
                "https://kunde.example/v1/epost/kilder/callback")
    cookie, csrf = _adminsesjon()
    r = _start(klient, cookie, csrf, _postboks())
    assert r.status_code == 200, r.text
    assert _autorisasjonsparametre(r)["redirect_uri"] == \
        "https://kunde.example/v1/epost/kilder/callback"


@pg
def test_ugyldig_redirect_uri_er_ukonfigurert_ikke_en_omdirigering(klient,
                                                                   m365):
    """Fail-closed på konfigfeil, som tenantsegmentet: en redirect-URI
    som ikke er husets egen callback over https gjør flyten
    UKONFIGURERT — den blir aldri en omdirigering til et fremmed sted."""
    cookie, csrf = _adminsesjon()
    for ugyldig in ("http://kunde.example/v1/epost/kilder/callback",
                    "https://kunde.example/annet",
                    "https://kunde.example/v1/epost/kilder/callback?x=1",
                    "https://kunde.example/v1/epost/kilder/callback#f",
                    "https://ond@kunde.example/v1/epost/kilder/callback",
                    "https:///v1/epost/kilder/callback"):
        m365.setenv("DISPONIT_M365_REDIRECT_URI", ugyldig)
        r = _start(klient, cookie, csrf, _postboks())
        assert r.status_code == 503, (ugyldig, r.text)
        assert r.json()["feil"] == "m365_ikke_konfigurert"


def test_m365_konfigurasjonen_er_koblet_hele_veien():
    """De fem M365-variablene skal nå API-ets prosess — alle tre leddene.

    🔴 KJEDEN ER TRE LEDD, OG ETT MANGLET (1/9). `epost_kilde` leser
    `os.environ`, men API-uniten bruker ikke `EnvironmentFile`: den henter
    hver hemmelighet med `LoadCredential` fra `/etc/disponit/api/<NAVN>`,
    og `opp.sh` er den som materialiserer filene fra miljøfila. PR-B var
    ferdig og merget mens INGEN av de fem sto i noen av de to filene —
    modulen ville svart «ikke konfigurert» uansett hva eier la i
    staging.env, og feilen ville sett ut som en Azure-feil.

    Hydreringen (`hemmeligheter.last_credentials`) itererer over HELE
    katalogen og trenger ingen liste — derfor måles bare de to leddene
    som faktisk har en liste å glemme noe fra.
    """
    rot = Path(__file__).resolve().parents[3]
    opp = (rot / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    unit = (rot / "deploy/staging/disponit-api.service").read_text(
        encoding="utf-8")
    for navn in ("DISPONIT_M365_CLIENT_ID", "DISPONIT_M365_CLIENT_SECRET",
                 "DISPONIT_M365_REDIRECT_URI", "DISPONIT_M365_TENANT",
                 "DISPONIT_M365_ALLOWLIST"):
        assert f"skriv_cred api {navn}" in opp.replace("  ", " ") \
            or f"skriv_cred api {navn}" in " ".join(opp.split()), \
            f"opp.sh materialiserer ikke {navn} — fila API-et leser blir"
        assert f"LoadCredential={navn}:/etc/disponit/api/{navn}" in unit, \
            f"{navn} lastes ikke inn i API-uniten — verdien når aldri" \
            " prosessen, uansett hva som står i miljøfila"
    # …og hemmeligheten skal KUN komme fra LoadCredential, aldri fra en
    # EnvironmentFile hele prosesstreet kan lese. Målt som: hver linje i
    # uniten som nevner variabelen ER en LoadCredential-linje.
    #
    # Første versjon av denne asserten splittet fila på «EnvironmentFile»
    # og lette i halen — som selvsagt inneholder LoadCredential-linjene
    # også, så den slo ut på seg selv. Den nye leser linje for linje.
    for linje in unit.splitlines():
        if "DISPONIT_M365_CLIENT_SECRET" not in linje:
            continue
        assert linje.strip().startswith("LoadCredential="), \
            f"klienthemmeligheten eksponeres utenfor LoadCredential: {linje!r}"
