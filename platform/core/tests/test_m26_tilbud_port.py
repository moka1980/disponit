"""Porten for ARC B tilbud, PR 1: tilbudsregisteret ved siden av boka.

  1. Et tilbud FØDES av boka: linjene får prisversjonen og listeprisen
     på tilbudsdatoen, summen regnes av døra, standardklausulene bindes
     ved sin hash. Svaret og lista bærer masken, aldri adressen.
  2. Et produkt uten pris på datoen, et deaktivert produkt eller en
     enhetspris over boka → 409 `tilbud_ulovlig_tilstand`; ukjent
     produkt → 404; kropp uten linjer / ugyldig adresse → 400.
  3. SP-2: samme Idempotency-Key gir samme tilbud (ny=false).
  4. Dommen: utkast → godkjent/forkastet; et avgjort tilbud avgjøres
     ikke igjen (409); «sendt» er aldri en dom (400).
  5. De to faktaene policyen bygger på regnes av registeret:
     `priser_fra_boka` faller når en linje er rabattert under tenantens
     grense; `klausuler_uendret` faller når klausulen får ny versjon.
  6. Detaljen bærer linjene og klausulteksten tilbudet siterte — også
     etter at klausulen er erstattet.
  7. Rutene under /v1/prisbok er urørt (108-dommen står); tilbudet har
     sine egne fire.

MUTASJONER SOM DREPER DENNE: la døra ta kallerens enhetspris uten å
sammenligne med boka (2), regn `priser_fra_boka` alltid sann (5), legg
adressen i lista (1).
"""
import secrets
import uuid

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg, token)
from .test_m17_avsender_port import _post
from .test_m26_prisbok import _pris, _produkt, _terskler
from .test_m37 import _sett_kontekst


def _tok(token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    return tok


def _rt():
    from db.pg import koble
    return koble(DSN)


def _klausul(c, tenant, kode="BET-14", tittel="Betaling", tekst="14 dager",
             standard=True, fra="2020-01-01"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m26_sett_klausul(%s,%s,%s,%s,%s,%s::date,'u-test')",
                  (tenant, kode, tittel, tekst, standard, fra)).fetchone()[0]
    c.commit()
    return v


def _rigg(*, rabatt=150):
    """Terskler, to produkter med pris (og ett uten), én standardklausul."""
    c = _rt()
    try:
        _terskler(c, TENANT, rabatt=rabatt)
        kabel = _produkt(c, TENANT, navn="Kabel PFXP 3x2,5", enhet="m")
        _pris(c, TENANT, kabel, 1250, "2024-01-01")
        pumpe = _produkt(c, TENANT, navn="Varmepumpe 6 kW", enhet="stk")
        _pris(c, TENANT, pumpe, 2890000, "2026-03-01")
        uten = _produkt(c, TENANT, navn="Montørtime", enhet="t")
        _klausul(c, TENANT, kode="BET-" + secrets.token_hex(2))
        return kabel, pumpe, uten
    finally:
        c.close()


def _kropp(linjer, **over):
    return {"kunde_navn": "Tromsø Borettslag", "kunde_epost":
            "styret-" + secrets.token_hex(3) + "@nordvik.example",
            "gyldig_til": "2026-12-31", "innledning": "Takk for befaringen.",
            "linjer": linjer, **over}


@pg
def test_tilbudet_fodes_av_boka_og_lista_baerer_masken(klient, migrator,
                                                        token):
    kabel, pumpe, _ = _rigg()
    tok = _tok(token)
    kropp = _kropp([{"produkt_id": str(kabel), "antall": 40},
                    {"produkt_id": str(pumpe), "antall": 1,
                     "enhetspris_ore": 2800000}],
                   tilbudsdato="2026-09-10")
    r = _post(klient, tok, "/v1/tilbud", kropp)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ny"] is True and d["sum_ore"] == 40 * 1250 + 2800000
    assert d["kunde_maske"].startswith("s****@nordvik.example")
    assert kropp["kunde_epost"] not in r.text
    tid = d["tilbud_id"]
    hode = {"authorization": f"Bearer {tok}"}
    r = klient.get("/v1/tilbud", headers=hode)
    assert r.status_code == 200, r.text
    assert kropp["kunde_epost"] not in r.text
    t = [x for x in r.json()["tilbud"] if x["tilbud_id"] == tid][0]
    assert t["status"] == "utkast" and t["antall_linjer"] == 2
    assert t["priser_fra_boka"] is True and t["klausuler_uendret"] is True
    assert t["kunde_maske"] == d["kunde_maske"] and t["tilbudsdato"] == "2026-09-10"
    r = klient.get(f"/v1/tilbud/{tid}", headers=hode)
    assert r.status_code == 200, r.text
    det = r.json()
    assert kropp["kunde_epost"] not in r.text
    assert [l["listepris_ore"] for l in det["linjer"]] == [1250, 2890000]
    assert [l["enhetspris_ore"] for l in det["linjer"]] == [1250, 2800000]
    assert det["linjer"][0]["prisversjon"] >= 1
    assert det["linjer"][1]["linjesum_ore"] == 2800000
    assert det["innledning"] == "Takk for befaringen."
    assert det["klausuler"] and all(k["tekst"] for k in det["klausuler"])
    # Adressen ligger kryptert, masken og hashen ved siden av.
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT kunde_kryptert IS NOT NULL, kunde_maske, kunde_hash"
        " FROM tilbud WHERE tenant=%s AND tilbud_id=%s", (TENANT, tid)
    ).fetchone()
    ev = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m26_prisbok' AND handling='tilbud.opprettet'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert rad[0] and rad[1] == d["kunde_maske"] and len(rad[2]) == 64
    assert ev >= 1


@pg
@dekker("tilbud_ulovlig_tilstand")
def test_boka_setter_grensene(klient, migrator, token):
    kabel, _, uten = _rigg()
    tok = _tok(token)
    # Produkt uten pris på datoen.
    r = _post(klient, tok, "/v1/tilbud",
              _kropp([{"produkt_id": str(uten), "antall": 2}]))
    assert r.status_code == 409 and \
        r.json()["feil"] == "tilbud_ulovlig_tilstand", r.text
    # Over boka.
    r = _post(klient, tok, "/v1/tilbud",
              _kropp([{"produkt_id": str(kabel), "antall": 1,
                       "enhetspris_ore": 1300}]))
    assert r.status_code == 409, r.text
    # Ukjent produkt.
    r = _post(klient, tok, "/v1/tilbud",
              _kropp([{"produkt_id": str(uuid.uuid4()), "antall": 1}]))
    assert r.status_code == 404, r.text
    # Uten linjer, ugyldig adresse, antall 0.
    for kropp in (_kropp([]), _kropp([{"produkt_id": str(kabel), "antall": 1}],
                                      kunde_epost="ikke en adresse"),
                  _kropp([{"produkt_id": str(kabel), "antall": 0}])):
        r = _post(klient, tok, "/v1/tilbud", kropp)
        assert r.status_code == 400, r.text


@pg
def test_samme_nokkel_gir_samme_tilbud(klient, migrator, token):
    kabel, _, _ = _rigg()
    tok = _tok(token)
    nokkel = secrets.token_urlsafe(24)
    kropp = _kropp([{"produkt_id": str(kabel), "antall": 5}])
    h = {"authorization": f"Bearer {tok}", "Idempotency-Key": nokkel}
    r1 = klient.post("/v1/tilbud", json=kropp, headers=h)
    r2 = klient.post("/v1/tilbud", json=kropp, headers=h)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["tilbud_id"] == r2.json()["tilbud_id"]
    assert r1.json()["ny"] is True
    assert r2.json()["ny"] is False


@pg
def test_dommen_gaar_bare_fra_utkast(klient, migrator, token):
    kabel, _, _ = _rigg()
    tok = _tok(token)
    tid = _post(klient, tok, "/v1/tilbud",
                _kropp([{"produkt_id": str(kabel), "antall": 5}])
                ).json()["tilbud_id"]
    r = _post(klient, tok, f"/v1/tilbud/{tid}/dom", {"status": "sendt"})
    assert r.status_code == 400, r.text
    r = _post(klient, tok, f"/v1/tilbud/{tid}/dom", {"status": "godkjent"})
    assert r.status_code == 200 and r.json()["ny"] is True, r.text
    r = _post(klient, tok, f"/v1/tilbud/{tid}/dom", {"status": "godkjent"})
    assert r.status_code == 200 and r.json()["ny"] is False       # stille ja
    r = _post(klient, tok, f"/v1/tilbud/{tid}/dom", {"status": "forkastet"})
    assert r.status_code == 409, r.text
    hode = {"authorization": f"Bearer {tok}"}
    t = [x for x in klient.get("/v1/tilbud", headers=hode).json()["tilbud"]
         if x["tilbud_id"] == tid][0]
    assert t["status"] == "godkjent" and t["avgjort_av"].startswith("token:")
    r = _post(klient, tok, f"/v1/tilbud/{uuid.uuid4()}/dom",
              {"status": "godkjent"})
    assert r.status_code == 404, r.text


@pg
def test_faktaene_regnes_av_registeret(klient, migrator, token):
    kabel, _, _ = _rigg(rabatt=100)       # 10 % er grensen
    tok = _tok(token)
    hode = {"authorization": f"Bearer {tok}"}
    # 12 % rabatt: under grensen → priser_fra_boka usant.
    tid_rab = _post(klient, tok, "/v1/tilbud",
                    _kropp([{"produkt_id": str(kabel), "antall": 10,
                             "enhetspris_ore": 1100}])).json()["tilbud_id"]
    # 8 % rabatt: innenfor.
    tid_ok = _post(klient, tok, "/v1/tilbud",
                   _kropp([{"produkt_id": str(kabel), "antall": 10,
                            "enhetspris_ore": 1150}])).json()["tilbud_id"]
    rader = {x["tilbud_id"]: x for x in
             klient.get("/v1/tilbud", headers=hode).json()["tilbud"]}
    assert rader[tid_rab]["priser_fra_boka"] is False
    assert rader[tid_ok]["priser_fra_boka"] is True
    assert rader[tid_ok]["klausuler_uendret"] is True
    # Klausulen får ny versjon → det bundne tilbudet siterer en gammel.
    det = klient.get(f"/v1/tilbud/{tid_ok}", headers=hode).json()
    kode = det["klausuler"][0]["kode"]
    c = _rt()
    try:
        _klausul(c, TENANT, kode=kode, tekst="30 dager", fra="2026-09-01")
    finally:
        c.close()
    rader = {x["tilbud_id"]: x for x in
             klient.get("/v1/tilbud", headers=hode).json()["tilbud"]}
    assert rader[tid_ok]["klausuler_uendret"] is False
    det2 = klient.get(f"/v1/tilbud/{tid_ok}", headers=hode).json()
    k = [x for x in det2["klausuler"] if x["kode"] == kode][0]
    assert k["tekst"] == "14 dager"          # den siterte versjonen står


def test_rutene_er_tilbudets_egne_og_prisboka_er_uroert():
    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE if sti.startswith("/v1/tilbud"))
    assert mine == ["/v1/tilbud", "/v1/tilbud", "/v1/tilbud/avsender",
                    "/v1/tilbud/{tilbud_id:uuid}",
                    "/v1/tilbud/{tilbud_id:uuid}/dom"], mine
    assert dict(RUTESCOPE)[("GET", "/v1/tilbud")] == "okonomi:read"
    assert dict(RUTESCOPE)[("POST", "/v1/tilbud")] == "bestilling:opprett"
    prisbok = sorted(sti for _m, sti in RUTESCOPE
                     if sti.startswith("/v1/prisbok"))
    assert len(prisbok) == 8
