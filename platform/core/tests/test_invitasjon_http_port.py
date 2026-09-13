"""Porten for 195: invitasjonen gjennom HTTP-døra.

194 ga dørene; dette er veien en admin og en kollega faktisk går.

TO TING SKILLER DENNE FRA DE ANDRE RUTENE I HUSET:

  * RÅTOKENET RETURNERES ÉN GANG og lagres aldri. Basen har bare hashen, så
    den som får tak i basen kan se AT en invitasjon finnes — ikke bruke den.
    Lista returnerer derfor aldri tokenet, bare åtte tegn av hashen som et
    gjenkjennelsesmerke.
  * INNLØSNINGEN BRUKER REGISTRANTENS SCOPE. Autoriteten er TOKENET;
    scopet er bare det `_autentiser` krever (den er bygget for ett påkrevd
    scope og avviser `None`). Målt: en bruker med to medlemskap kan ikke
    logge inn før firmavelgeren finnes, så hver inviterte ER registrant.
    MÅ UTVIDES sammen med velgeren.

MUTASJONENE SOM DREPER DISSE:
  * la lista ta med `token_hash` i svaret        → port 3
  * gi `leser` scopet `firma:inviter`            → port 4
  * la innløsningen ta bruker-id fra KROPPEN     → port 7
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, app, klient,  # noqa: F401
                       migrator, miljo, pg)
from .test_m37 import _sett_kontekst

REG = "_registrering"


def _t():
    return "t-invh-" + secrets.token_hex(3)


def _identitet(c, merke):
    _sett_kontekst(c, "t-invh-oppsett")
    bid = c.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://invh.test', %s) RETURNING bruker_id",
        (f"{merke}-" + secrets.token_hex(6),)).fetchone()[0]
    c.commit()
    return bid


def _okt(c, tenant, bid, roller):
    """En ekte browsersesjon med gitte roller i gitt tenant."""
    from api import sesjon as sesjonmodul

    _sett_kontekst(c, tenant)
    c.execute("INSERT INTO brukermedlemskap (tenant, bruker_id, roller,"
              " aktiv) VALUES (%s,%s,%s,true)"
              " ON CONFLICT (tenant, bruker_id) DO UPDATE SET roller=%s",
              (tenant, bid, list(roller), list(roller)))
    ver = c.execute("SELECT authz_version FROM brukermedlemskap WHERE"
                    " tenant=%s AND bruker_id=%s", (tenant, bid)).fetchone()[0]
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    c.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, utloper)"
        " VALUES (%s,%s,%s,%s,%s, now() + interval '10 hours')",
        (sesjonmodul._hash(cookie), tenant, bid, ver,
         sesjonmodul._hash(csrf)))
    c.commit()
    return cookie, csrf


def _firma(c, t):
    _sett_kontekst(c, t)
    c.execute("SELECT firma_registrer(%s,'Invitasjonsfirma AS',NULL,30,"
              "'kari')", (t,))
    c.commit()


def _kall(klient, sti, cookie, csrf, kropp=None, metode="POST"):
    from api import sesjon as sesjonmodul
    hoder = {"X-Disponit-CSRF": csrf,
             "Idempotency-Key": secrets.token_hex(16)}
    kaker = {sesjonmodul.C_SESJON: cookie, sesjonmodul.C_CSRF: csrf}
    if metode == "GET":
        return klient.get(sti, cookies=kaker, headers=hoder)
    return klient.post(sti, json=kropp or {}, cookies=kaker, headers=hoder)


# ---------------------------------------------------------------------------
# 1-3. Admin lager lenken.
# ---------------------------------------------------------------------------

@pg
def test_admin_far_raatokenet_en_gang_og_basen_bare_hashen(miljo, migrator,
                                                           klient):
    import hashlib

    t = _t()
    _firma(migrator, t)
    admin = _identitet(migrator, "a")
    cookie, csrf = _okt(migrator, t, admin, ["admin"])

    r = _kall(klient, "/v1/invitasjoner", cookie, csrf,
              {"roller": ["leser"], "timer": 24})
    assert r.status_code in (200, 201), r.text
    token = r.json()["token"]
    assert len(token) >= 40, "tokenet er for kort til å være en hemmelighet"

    _sett_kontekst(migrator, t)
    rad = migrator.execute("SELECT token_hash FROM firmainvitasjon"
                           " WHERE tenant=%s", (t,)).fetchone()
    assert rad[0] == hashlib.sha256(token.encode()).hexdigest()
    # RÅTOKENET FINNES IKKE i noen kolonne.
    treff = migrator.execute(
        "SELECT count(*) FROM firmainvitasjon WHERE tenant=%s"
        "   AND firmainvitasjon::text LIKE %s", (t, f"%{token}%")).fetchone()[0]
    assert treff == 0, "råtokenet ble lagret"


@pg
def test_lista_viser_aldri_tokenet(miljo, migrator, klient):
    """Lista står bak `security:read`, ikke `firma:inviter` — CI fanget at
    en GET med et MUTERENDE scope bryter husets kontrakt (`test_pr008`:
    «leserute med ikke-lese-scope»). En browsersesjon måles mot nettopp
    `LESESCOPES` for lesing.

    Scopet er dessuten riktig på innholdet: hvem som blir gitt tilgang til
    firmaet er sikkerhetsinformasjon.
    """
    t = _t()
    _firma(migrator, t)
    admin = _identitet(migrator, "a")
    cookie, csrf = _okt(migrator, t, admin, ["admin"])
    token = _kall(klient, "/v1/invitasjoner", cookie, csrf,
                  {"roller": ["leser"]}).json()["token"]

    r = _kall(klient, "/v1/invitasjoner", cookie, csrf, metode="GET")
    assert r.status_code == 200, r.text
    tekst = r.text
    assert token not in tekst, "lista lekket råtokenet"
    inv = r.json()["invitasjoner"]
    assert len(inv) == 1
    assert len(inv[0]["merke"]) == 8, "merket skal være åtte tegn, ikke hashen"
    assert "token" not in inv[0] and "token_hash" not in inv[0]
    assert inv[0]["brukt"] is None


@pg
def test_en_leser_kan_ikke_invitere(miljo, migrator, klient):
    """Å slippe inn en kollega er å dele ut fullmakter. En `leser` som kunne
    invitere, kunne invitert seg selv en ny konto med flere roller."""
    t = _t()
    _firma(migrator, t)
    leser = _identitet(migrator, "l")
    cookie, csrf = _okt(migrator, t, leser, ["leser"])

    r = _kall(klient, "/v1/invitasjoner", cookie, csrf, {"roller": ["admin"]})
    assert r.status_code in (401, 403), r.text
    _sett_kontekst(migrator, t)
    assert migrator.execute("SELECT count(*) FROM firmainvitasjon"
                            " WHERE tenant=%s", (t,)).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 4-6. Kollegaen innløser.
# ---------------------------------------------------------------------------

@pg
def test_kollegaen_blir_medlem_med_registrantens_scope(miljo, migrator,
                                                      klient):
    t = _t()
    _firma(migrator, t)
    admin, ny = _identitet(migrator, "a"), _identitet(migrator, "n")
    ac, acsrf = _okt(migrator, t, admin, ["admin"])
    token = _kall(klient, "/v1/invitasjoner", ac, acsrf,
                  {"roller": ["leser", "godkjenner"]}).json()["token"]

    # Den inviterte er REGISTRANT: ett scope, og det er ikke `firma:inviter`.
    #
    # BINDINGEN SOM MÅ UTVIDES MED FIRMAVELGEREN: ruten krever
    # `firma:opprett`, som bare registranten har. I dag er det riktig —
    # en bruker med to medlemskap kan ikke logge inn før velgeren finnes
    # (192), så hver inviterte ER registrant. Den dagen velgeren kommer,
    # vil en ansatt i firma A ikke kunne innløse en invitasjon til firma B,
    # og scopet må utvides. Denne porten er stedet å oppdage det.
    migrator.execute("SELECT registrant_medlemskap(%s)", (ny,))
    migrator.commit()
    nc, ncsrf = _okt(migrator, REG, ny, ["registrant"])

    r = _kall(klient, "/v1/invitasjoner/innloes", nc, ncsrf,
              {"tenant": t, "token": token})
    assert r.status_code in (200, 201), r.text
    assert sorted(r.json()["roller"]) == ["godkjenner", "leser"]

    _sett_kontekst(migrator, t)
    assert migrator.execute(
        "SELECT roller FROM brukermedlemskap WHERE tenant=%s AND bruker_id=%s",
        (t, ny)).fetchone()[0] == ["leser", "godkjenner"]


@pg
def test_lenken_virker_bare_en_gang_over_http(miljo, migrator, klient):
    t = _t()
    _firma(migrator, t)
    admin = _identitet(migrator, "a")
    en, to = _identitet(migrator, "1"), _identitet(migrator, "2")
    ac, acsrf = _okt(migrator, t, admin, ["admin"])
    token = _kall(klient, "/v1/invitasjoner", ac, acsrf,
                  {"roller": ["leser"]}).json()["token"]

    c1, s1 = _okt(migrator, REG, en, ["registrant"])
    assert _kall(klient, "/v1/invitasjoner/innloes", c1, s1,
                 {"tenant": t, "token": token}).status_code in (200, 201)

    c2, s2 = _okt(migrator, REG, to, ["registrant"])
    r = _kall(klient, "/v1/invitasjoner/innloes", c2, s2,
              {"tenant": t, "token": token})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "invitasjon_ugyldig"


@pg
def test_feil_firma_gir_samme_svar_som_feil_token(miljo, migrator, klient):
    """Å skille avslagene ville latt noen prøve seg fram og lære hvilke
    tokener og firmaer som finnes."""
    t, annet = _t(), _t()
    _firma(migrator, t)
    _firma(migrator, annet)
    admin, ny = _identitet(migrator, "a"), _identitet(migrator, "n")
    ac, acsrf = _okt(migrator, t, admin, ["admin"])
    token = _kall(klient, "/v1/invitasjoner", ac, acsrf,
                  {"roller": ["leser"]}).json()["token"]
    nc, ncsrf = _okt(migrator, REG, ny, ["registrant"])

    feil_firma = _kall(klient, "/v1/invitasjoner/innloes", nc, ncsrf,
                       {"tenant": annet, "token": token})
    feil_token = _kall(klient, "/v1/invitasjoner/innloes", nc, ncsrf,
                       {"tenant": t, "token": "finnes-ikke-" + secrets.token_urlsafe(20)})
    assert feil_firma.status_code == feil_token.status_code == 409
    assert feil_firma.json()["feil"] == feil_token.json()["feil"]


# ---------------------------------------------------------------------------
# 7-8. Identiteten kommer fra SESJONEN, og CSRF gjelder selv uten scope.
# ---------------------------------------------------------------------------

@pg
def test_innloesningen_binder_sesjonens_bruker_ikke_kroppens(miljo, migrator,
                                                            klient):
    """Kroppen kan si hva den vil om hvem hun er. `_browserkontekst` gir
    bruker-id-en fra ØKTEN — ellers kunne hvem som helst meldt inn en annen."""
    t = _t()
    _firma(migrator, t)
    admin, ny, offer = (_identitet(migrator, "a"), _identitet(migrator, "n"),
                        _identitet(migrator, "o"))
    ac, acsrf = _okt(migrator, t, admin, ["admin"])
    token = _kall(klient, "/v1/invitasjoner", ac, acsrf,
                  {"roller": ["leser"]}).json()["token"]
    nc, ncsrf = _okt(migrator, REG, ny, ["registrant"])

    r = _kall(klient, "/v1/invitasjoner/innloes", nc, ncsrf,
              {"tenant": t, "token": token, "bruker_id": offer})
    assert r.status_code in (200, 201), r.text
    _sett_kontekst(migrator, t)
    medlemmer = {r0[0] for r0 in migrator.execute(
        "SELECT bruker_id FROM brukermedlemskap WHERE tenant=%s",
        (t,)).fetchall()}
    assert ny in medlemmer, "sesjonens bruker ble ikke medlem"
    assert offer not in medlemmer, "kroppen bestemte hvem som ble medlem"


@pg
def test_uten_csrf_slipper_ingen_inn(miljo, migrator,
                                                         klient):
    from api import sesjon as sesjonmodul

    t = _t()
    _firma(migrator, t)
    admin, ny = _identitet(migrator, "a"), _identitet(migrator, "n")
    ac, acsrf = _okt(migrator, t, admin, ["admin"])
    token = _kall(klient, "/v1/invitasjoner", ac, acsrf,
                  {"roller": ["leser"]}).json()["token"]
    nc, ncsrf = _okt(migrator, REG, ny, ["registrant"])

    # Riktig sesjon, men ingen CSRF-header.
    r = klient.post("/v1/invitasjoner/innloes",
                    json={"tenant": t, "token": token},
                    cookies={sesjonmodul.C_SESJON: nc,
                             sesjonmodul.C_CSRF: ncsrf},
                    headers={"Idempotency-Key": secrets.token_hex(16)})
    assert r.status_code in (400, 401, 403), r.text
    _sett_kontekst(migrator, t)
    assert migrator.execute(
        "SELECT count(*) FROM brukermedlemskap WHERE tenant=%s AND"
        " bruker_id=%s", (t, ny)).fetchone()[0] == 0


@pg
def test_sikkerhetsrollen_ser_lista_men_kan_ikke_invitere(miljo, migrator,
                                                          klient):
    """Skillet er hele grunnen til at lista har sitt eget scope: å SE hvem
    som er sluppet inn, og å SLIPPE NOEN INN, er to fullmakter."""
    t = _t()
    _firma(migrator, t)
    admin, vakt = _identitet(migrator, "a"), _identitet(migrator, "s")
    ac, acsrf = _okt(migrator, t, admin, ["admin"])
    _kall(klient, "/v1/invitasjoner", ac, acsrf, {"roller": ["leser"]})

    sc, scsrf = _okt(migrator, t, vakt, ["sikkerhet"])
    lese = _kall(klient, "/v1/invitasjoner", sc, scsrf, metode="GET")
    assert lese.status_code == 200, lese.text
    assert len(lese.json()["invitasjoner"]) == 1

    skrive = _kall(klient, "/v1/invitasjoner", sc, scsrf,
                   {"roller": ["admin"]})
    assert skrive.status_code in (401, 403), skrive.text
