"""Porten for ADR-012: Gmail-kilden koblet til med Googles egen innlogging.

  1. Scopet er LUKKET: ett, det IMAP og SMTP krever. Tokenet reiser som
     XOAUTH2 mot Gmail — over IMAP og over SMTP — og et avslag er et avslag.
  2. /google/start gir Googles authorize-URL med det som skal til for å få
     et refresh-token, og en state som sier at den er Googles.
  3. Callbacken BEVISER at samtykket gjelder den oppgitte postboksen før noe
     lagres. Logget hun inn med en annen konto, skrives ingen kilde.
  4. Kilden er en IMAP-kilde: samme runde henter den, med et kortlivet
     token i stedet for et app-passord.
  5. Google sier nei til fornyelsen -> kilden feiler. Google svarer ikke,
     eller innloggingen er ikke satt opp -> kilden står urørt.
  6. Uten konfig svarer /google/start den ærlige koden, og Microsoft-veien
     merker ingenting.

MUTASJONER SOM DREPER DENNE: lagre før IMAP-innloggingen er prøvd (3), la
et nettverksbrudd mot Google felle kilden (5), dropp `prompt=consent` (2),
send tokenet som LOGIN-passord (1).

Google er MOCKET: kodevekslingen og fornyelsen byttes ut, og Gmail er en
etterligning. Den ekte runden mot Google er ikke prøvd i test.
"""
from __future__ import annotations

import base64
import ipaddress
import secrets
from urllib.parse import urlsplit

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg)
from .test_m37 import _sett_kontekst
from .test_m6_imap_port import _Postboks, _kilde, _mine, _raa
from .test_m6_inntak_port import _m365, _pa, pg_plan
from .test_m6_kilde import (_autorisasjonsparametre, _binding, _callback,
                            _konvolutt)
from .test_outbox_bestilling import _adminsesjon

G_ID = "g-klient-id.apps.googleusercontent.example"
G_SECRET = "g-klient-hemmelighet-" + secrets.token_hex(6)
REFRESH = "google-refresh-" + secrets.token_hex(12)
ACCESS = "google-access-" + secrets.token_hex(12)


@pytest.fixture()
def google(monkeypatch):
    from api import ssrf
    monkeypatch.setenv("DISPONIT_GOOGLE_CLIENT_ID", G_ID)
    monkeypatch.setenv("DISPONIT_GOOGLE_CLIENT_SECRET", G_SECRET)
    monkeypatch.delenv("DISPONIT_GOOGLE_REDIRECT_URI", raising=False)
    monkeypatch.delenv("DISPONIT_M365_ALLOWLIST", raising=False)
    monkeypatch.setattr(
        ssrf, "_resolv",
        lambda h, p: [ipaddress.ip_address("142.250.74.1")])
    return monkeypatch


def _postboks() -> str:
    return f"felles-{secrets.token_hex(5)}@gmail.example"


def _start(klient_, cookie, csrf, postboks, nokkel=None):
    from api import sesjon as sesjonmodul
    return klient_.post(
        "/v1/epost/kilder/google/start", json={"postboks": postboks},
        headers={"X-Disponit-CSRF": csrf,
                 "Idempotency-Key": nokkel or ("g-" + secrets.token_hex(8))},
        cookies={sesjonmodul.C_SESJON: cookie})


def _rad(m, postboks):
    _sett_kontekst(m, TENANT)
    r = m.execute(
        "SELECT kilde_id, leverandor, status, scope, auth_kryptert, nonce,"
        " key_id, epost_kilde::text FROM epost_kilde"
        " WHERE tenant=%s AND postboks=%s", (TENANT, postboks)).fetchone()
    m.rollback()
    return r


# ---------------------------------------------------------------------------
# 1. Scopet og XOAUTH2
# ---------------------------------------------------------------------------

def test_scopet_er_lukket_og_konfigen_er_valgfri(monkeypatch):
    from api import epost_kilde as k
    assert k.GOOGLE_SCOPE == "https://mail.google.com/"
    assert k.kan_svare(k.GOOGLE_KILDESCOPE) is True
    monkeypatch.delenv("DISPONIT_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("DISPONIT_GOOGLE_CLIENT_SECRET", raising=False)
    assert k.hent_google_konfig() is None
    monkeypatch.setenv("DISPONIT_GOOGLE_CLIENT_ID", G_ID)
    assert k.hent_google_konfig() is None, "halv konfig er ingen konfig"
    monkeypatch.setenv("DISPONIT_GOOGLE_CLIENT_SECRET", G_SECRET)
    konfig = k.hent_google_konfig()
    assert urlsplit(konfig.autoriser_endepunkt).hostname \
        == "accounts.google.com"
    assert urlsplit(konfig.token_endepunkt).hostname \
        == "oauth2.googleapis.com"
    # En redirect-URI som ikke er husets egen callback er en feilkonfig.
    monkeypatch.setenv("DISPONIT_GOOGLE_REDIRECT_URI",
                       "https://ond.example/v1/epost/kilder/callback?x=1")
    assert k.hent_google_konfig() is None


def test_et_token_logger_inn_med_xoauth2_og_et_passord_med_login(
        monkeypatch):
    import imaplib
    import smtplib
    from email.message import EmailMessage
    from plan import epost_imap as e
    sett = []

    class FalskImap:
        def __init__(self, *a, **k):
            pass

        def login(self, bruker, passord):
            sett.append(("login", bruker, passord))

        def authenticate(self, mekanisme, svar):
            sett.append(("authenticate", mekanisme, svar(b"")))
            if b"avvis" in svar(b""):
                raise imaplib.IMAP4.error("nei")

        def logout(self):
            pass
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FalskImap)
    e.Imap("gmail", "post@gmail.example", "abcdefghijklmnop")
    assert sett == [("login", "post@gmail.example", "abcdefghijklmnop")]
    sett.clear()
    e.Imap("gmail", "post@gmail.example", e.Bearer("tok-1"))
    assert sett == [("authenticate", "XOAUTH2",
                     b"user=post@gmail.example\x01auth=Bearer tok-1\x01\x01")]
    with pytest.raises(e.ImapAvvist):
        e.Imap("gmail", "post@gmail.example", e.Bearer("avvis"))

    svar = {"kode": 235}

    class FalskSmtp:
        def __init__(self, *a, **k):
            pass

        def ehlo(self):
            sett.append(("ehlo",))

        def login(self, bruker, passord):
            sett.append(("login", bruker, passord))

        def docmd(self, kommando, arg=""):
            sett.append(("docmd", kommando, arg))
            if kommando == "AUTH":
                return svar["kode"], b""
            return 535, b"nei"

        def send_message(self, m):
            sett.append(("sendt",))

        def quit(self):
            pass
    monkeypatch.setattr(smtplib, "SMTP_SSL", FalskSmtp)
    m = EmailMessage()
    sett.clear()
    e.send_smtp("gmail", "post@gmail.example", e.Bearer("tok-2"), m)
    forventet = base64.b64encode(
        b"user=post@gmail.example\x01auth=Bearer tok-2\x01\x01").decode()
    assert ("docmd", "AUTH", "XOAUTH2 " + forventet) in sett
    assert ("sendt",) in sett and not [s for s in sett if s[0] == "login"]
    # Google forklarer et avslag i en utfordring (334) før den sier 535.
    sett.clear()
    svar["kode"] = 334
    with pytest.raises(e.ImapAvvist):
        e.send_smtp("gmail", "post@gmail.example", e.Bearer("tok-3"), m)
    assert ("sendt",) not in sett, "sendte etter en avvist innlogging"
    sett.clear()
    e.send_smtp("gmail", "post@gmail.example", "abcdefghijklmnop", m)
    assert ("login", "post@gmail.example", "abcdefghijklmnop") in sett


# ---------------------------------------------------------------------------
# 2–3. /google/start og callbacken
# ---------------------------------------------------------------------------

@pg
def test_start_gir_googles_url_og_en_state_som_er_googles(klient, migrator,
                                                         google):
    cookie, csrf = _adminsesjon()
    postboks = _postboks()
    r = _start(klient, cookie, csrf, postboks)
    assert r.status_code == 200, r.text
    url = r.json()["autorisasjonsurl"]
    delt = urlsplit(url)
    assert (delt.scheme, delt.hostname, delt.path) == (
        "https", "accounts.google.com", "/o/oauth2/v2/auth")
    p = _autorisasjonsparametre(r)
    assert p["client_id"] == G_ID and p["response_type"] == "code"
    assert p["scope"] == "https://mail.google.com/"
    # Uten disse to gir Google ikke et refresh-token ved ny tilkobling.
    assert p["access_type"] == "offline" and p["prompt"] == "consent"
    assert p["login_hint"] == postboks
    assert p["code_challenge_method"] == "S256" and p["code_challenge"]
    assert p["redirect_uri"].endswith("/v1/epost/kilder/callback")
    assert "response_mode" not in p
    assert G_SECRET not in url
    assert _konvolutt(p["state"])["payload"]["lev"] == "google"
    assert _binding(r)
    assert _rad(migrator, postboks) is None, "/start skrev en kilde"


@pg
def test_callbacken_beviser_postboksen_for_noe_lagres(klient, migrator,
                                                     google):
    from api import epost_kilde as k
    from plan import epost_imap
    cookie, csrf = _adminsesjon()
    provd, vekslet = [], {}

    def veksle(konfig, code, verifier, redirect_uri):
        vekslet.update(code=code, verifier=verifier, client=konfig.client_id,
                       endepunkt=konfig.token_endepunkt)
        return {"refresh_token": REFRESH, "access_token": ACCESS,
                "expires_in": 3599}
    google.setattr(k, "_veksle_google_kode", veksle)
    # Microsofts veksler skal ALDRI røres av en Google-state.
    google.setattr(k, "_veksle_kode", lambda *a: pytest.fail("feil dør"))

    # Hun logget inn med EN ANNEN konto: Gmail sier nei for adressen.
    def avvis(tjeneste, bruker, passord):
        provd.append((tjeneste, bruker, passord))
        raise epost_imap.ImapAvvist("nei")
    google.setattr(epost_imap, "prov_innlogging", avvis)
    feil = _postboks()
    r = _start(klient, cookie, csrf, feil)
    cb = _callback(klient, _autorisasjonsparametre(r)["state"], _binding(r))
    assert cb.status_code == 400, cb.text
    assert _rad(migrator, feil) is None, "kilden ble lagret uten bevis"
    assert provd and provd[-1][:2] == ("gmail", feil)
    assert isinstance(provd[-1][2], epost_imap.Bearer)
    assert provd[-1][2] == ACCESS

    # Rett konto: kilden finnes, som en IMAP-kilde, kryptert.
    google.setattr(epost_imap, "prov_innlogging",
                   lambda *a: provd.append(a))
    postboks = _postboks()
    r = _start(klient, cookie, csrf, postboks)
    p = _autorisasjonsparametre(r)
    cb = _callback(klient, p["state"], _binding(r))
    assert cb.status_code == 303, cb.text
    assert cb.headers["location"] == "/#/epost"
    assert vekslet["client"] == G_ID
    assert vekslet["endepunkt"] == "https://oauth2.googleapis.com/token"
    rad = _rad(migrator, postboks)
    assert rad is not None
    _kid, leverandor, status, scope, ct, nonce, key_id, tekst = rad
    assert (leverandor, status, scope) == ("imap", "aktiv",
                                           "imap smtp google")
    for hemmelig in (REFRESH, ACCESS):
        assert hemmelig.encode() not in bytes(ct), "klartekst i basen"
        assert hemmelig not in tekst and hemmelig not in cb.text
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    dek = kryptering.hent_dek(migrator, TENANT, key_id)
    migrator.rollback()
    assert kryptering.dekrypter(dek, bytes(ct), bytes(nonce), TENANT,
                                key_id) == {"imap_tjeneste": "gmail",
                                            "google_refresh": REFRESH}
    # Staten er ENGANGS, også her.
    igjen = _callback(klient, p["state"], _binding(r))
    assert igjen.status_code == 400

    # Kilden vises som en postboks som kan svare — uten noe hemmelig.
    liste = klient.get("/v1/epost/kilder",
                       headers={"X-Disponit-CSRF": csrf},
                       cookies={__import__("api.sesjon", fromlist=["x"])
                                .C_SESJON: cookie})
    assert liste.status_code == 200, liste.text
    assert REFRESH not in liste.text
    mine = [x for x in liste.json()["kilder"] if x["postboks"] == postboks]
    assert mine and mine[0]["kan_svare"] is True

    # Uten refresh-token i svaret (Google gir det ikke uten samtykke-
    # runden) lagres ingenting.
    google.setattr(k, "_veksle_google_kode",
                   lambda *a: {"access_token": ACCESS})
    uten = _postboks()
    r = _start(klient, cookie, csrf, uten)
    cb = _callback(klient, _autorisasjonsparametre(r)["state"], _binding(r))
    assert cb.status_code == 400 and _rad(migrator, uten) is None


@pg
@dekker("google_ikke_konfigurert")
def test_uten_google_konfig_er_svaret_aerlig_og_microsoft_merker_ingenting(
        klient, migrator, monkeypatch):
    monkeypatch.delenv("DISPONIT_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("DISPONIT_GOOGLE_CLIENT_SECRET", raising=False)
    cookie, csrf = _adminsesjon()
    nokkel = "g-" + secrets.token_hex(8)
    r = _start(klient, cookie, csrf, _postboks(), nokkel)
    assert r.status_code == 503, r.text
    assert r.json()["feil"] == "google_ikke_konfigurert"
    assert "autorisasjonsurl" not in r.json() and _binding(r) is None
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM idempotens WHERE tenant=%s"
                         " AND nokkel=%s", (TENANT, nokkel)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "nøkkelen ble brent på en flyt som aldri fantes"


# ---------------------------------------------------------------------------
# 4–5. Runden
# ---------------------------------------------------------------------------

def _google_kilde(m):
    from api.epost_kilde import GOOGLE_KILDESCOPE
    from db import kryptering
    postboks = _postboks()
    _sett_kontekst(m, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, TENANT)
    ct, nonce = kryptering.krypter(
        dek, {"imap_tjeneste": "gmail", "google_refresh": REFRESH},
        TENANT, key_id)
    kid = m.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
        " auth_kryptert, nonce, key_id, scope) VALUES"
        " (%s,'imap',%s,%s,%s,%s,%s) RETURNING kilde_id",
        (TENANT, postboks, ct, nonce, key_id, GOOGLE_KILDESCOPE)).fetchone()[0]
    m.commit()
    return kid, postboks


@pg_plan
def test_runden_henter_med_et_kortlivet_token(migrator, miljo, app, google,
                                              capsys):
    from api import epost_kilde as k
    from plan import epost_imap
    from plan.epost_imap import kjor_en_runde
    _m365(google)
    kid, postboks = _google_kilde(migrator)
    fornyet = []

    def veksle(konfig, refresh):
        fornyet.append((konfig.client_id, refresh))
        return {"access_token": ACCESS, "expires_in": 3599}
    google.setattr(k, "_veksle_google_refresh", veksle)
    boks = _Postboks()
    pa = _pa()
    try:
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        assert _mine(res, kid)[0]["startet_fra"] == "naa", res
        tjeneste, bruker, passord = boks.innlogginger[-1]
        assert (tjeneste, bruker) == ("gmail", postboks)
        assert isinstance(passord, epost_imap.Bearer) and passord == ACCESS
        assert fornyet[-1] == (G_ID, REFRESH)
        boks.legg_til(_raa(emne="Ny", mid="<g1@mail.example>"))
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        assert _mine(res, kid)[0]["nye"] == 1, res
        assert _kilde(migrator, kid)[1] == 1
    finally:
        pa.close()
    logg = capsys.readouterr().out
    for ikke in (REFRESH, ACCESS, postboks):
        assert ikke not in logg, "token eller adresse i loggen"


@pg_plan
def test_googles_nei_feller_kilden_og_brudd_gjor_det_ikke(migrator, miljo,
                                                         app, google):
    from api import epost_kilde as k
    from plan import epost_imap
    from plan.epost_imap import kjor_en_runde
    _m365(google)
    kid, _pb = _google_kilde(migrator)
    boks = _Postboks()
    pa = _pa()

    def runde():
        return _mine(kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                                   sender=lambda *a: None), kid)
    try:
        # Google svarer ikke: drift. Kilden står, og Gmail ble aldri ringt.
        def nede(konfig, refresh):
            raise epost_imap.ImapForbigaende("google_token")
        google.setattr(k, "_veksle_google_refresh", nede)
        mine = runde()
        assert mine and "forbigaende" in mine[0], mine
        assert _kilde(migrator, kid)[0][0] == "aktiv"
        assert boks.innlogginger == []

        # Innloggingen er ikke satt opp på verten: heller ikke kundens feil.
        google.delenv("DISPONIT_GOOGLE_CLIENT_SECRET")
        mine = runde()
        assert mine and "forbigaende" in mine[0], mine
        assert _kilde(migrator, kid)[0][0] == "aktiv"
        google.setenv("DISPONIT_GOOGLE_CLIENT_SECRET", G_SECRET)

        # Google sier NEI til tokenet: kilden feiler, og må kobles til igjen.
        def nei(konfig, refresh):
            raise k.KildeFeil("google avviste fornyelsen")
        google.setattr(k, "_veksle_google_refresh", nei)
        runde()
        assert _kilde(migrator, kid)[0][0] == "feilet"
        assert boks.innlogginger == []
    finally:
        pa.close()


def test_fornyelsen_skiller_googles_dom_fra_drift(google):
    """400/401 er dommen over tokenet; alt annet er forbigående."""
    from api import epost_kilde as k
    from plan import epost_imap
    konfig = k.hent_google_konfig()
    sendt = {}

    def svar(status, kropp=None):
        def f(konfig_, data):
            sendt.update(data)
            return status, kropp or {}
        return f
    google.setattr(k, "_google_token", svar(200, {"access_token": "a"}))
    assert k._veksle_google_refresh(konfig, REFRESH) == {"access_token": "a"}
    assert sendt == {"grant_type": "refresh_token", "refresh_token": REFRESH}
    for status in (400, 401):
        google.setattr(k, "_google_token", svar(status))
        with pytest.raises(k.KildeFeil):
            k._veksle_google_refresh(konfig, REFRESH)
    for status in (429, 500, 503):
        google.setattr(k, "_google_token", svar(status))
        with pytest.raises(epost_imap.ImapForbigaende):
            k._veksle_google_refresh(konfig, REFRESH)

    def brudd(konfig_, data):
        raise k.KildeFeil("OSError")
    google.setattr(k, "_google_token", brudd)
    with pytest.raises(epost_imap.ImapForbigaende):
        k._veksle_google_refresh(konfig, REFRESH)
