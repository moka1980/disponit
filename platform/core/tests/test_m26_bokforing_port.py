"""Porten for ARC B tilbud, PR 5: kvitteringen når registeret.

  1. Hele veien: sending → signert `utfort`-kvittering → tilbudet er
     `sendt` med oppdraget og malversjonen, evidensen `tilbud.sendt` uten
     adresse. Samme kvittering én gang til er idempotent.
  2. `sendt` er aldri en dom: `POST …/dom {sendt}` → 400, og døra nekter
     et tilbud som ikke er godkjent.
  3. En kvittering for et annet tilbud / uten gyldig ressurs aksepteres,
     men bokføres ikke.
  4. Brudd-saken fra utløseren bærer referansen og summen — aldri
     adressen — og M-37s R1 planlegger et komplett oppdrag av den.

MUTASJONER SOM DREPER DENNE: fjern kroken i `_ingest_kvittering` (1),
sløyf ressurssammenligningen i `bokfor_tilbud_sendt` (4), la døra
sende et utkast (2 — vakten gjerder også, men porten leser dørens setning).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_tilbud_port import _btok, _klar, _tilbudpolicy
from .test_m17_avsender_port import _post
from .test_m26_sending_port import (MODUL, _Sender, _bestilt_av_utloseren,
                                    _kjor_til, _oppdrag, _release, _signer)
from .test_m26_tilbud_port import _rigg
from .test_m26_tilbudsutloser_port import _pa, _runde
from .test_m37 import _sett_kontekst

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _tilstand(migrator, tid):
    _sett_kontekst(migrator, TENANT)
    t = migrator.execute(
        "SELECT status, sendt_oppdrag_id, sendt_malversjon FROM tilbud"
        " WHERE tenant=%s AND tilbud_id=%s", (TENANT, tid)).fetchone()
    ev = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m26_prisbok' AND handling='tilbud.sendt'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    return t, ev


@pg_plan
def test_kvitteringen_setter_sendt_en_gang(migrator, miljo, app, klient, token):
    from modules.m26_prisbok import controller
    controller._sov = lambda s: None
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    adresse = "styret-" + secrets.token_hex(3) + "@nordvik.example"
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 4}],
                   kunde_epost=adresse)
    oid = _bestilt_av_utloseren(app, tid)
    _, ev_for = _tilstand(migrator, tid)
    mtk, _ = _onboard(klient, migrator)
    kvitteringer = []

    def signer(kropp):
        signert = _signer(kropp)
        kvitteringer.append(signert)
        return signert
    ut = _kjor_til(klient, mtk, _Sender(), signer, tid)
    assert ut["utfall"] == "utfort", ut
    t, ev = _tilstand(migrator, tid)
    assert t == ("sendt", oid, "tilbud-v1") and ev == ev_for + 1
    _sett_kontekst(migrator, TENANT)
    lekk = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND begrunnelse::text ILIKE %s", (TENANT, "%nordvik.example%")
    ).fetchone()[0]
    migrator.rollback()
    assert lekk == 0
    r = klient.post("/v1/oppdrag/kvittering", json=kvitteringer[-1],
                    headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 200, r.text
    assert _tilstand(migrator, tid) == (t, ev_for + 1)
    assert _oppdrag(migrator, oid)[0] == "utfort"
    hode = {"authorization": f"Bearer {tok}"}
    x = [y for y in klient.get("/v1/tilbud", headers=hode).json()["tilbud"]
         if y["tilbud_id"] == tid][0]
    assert x["status"] == "sendt"
    # Ikke kandidat lenger.
    pa = _pa()
    try:
        assert not [y for y in _runde(app, pa)["resultater"] if y["tilbud"] == tid]
    finally:
        pa.close()


def _onboard(klient, migrator):
    from .test_modul_onboarding_http import _onboard_token
    return _onboard_token(klient, migrator, MODUL, _release(migrator))


@pg
def test_sendt_er_aldri_en_dom(migrator, miljo, klient, token):
    from db.pg import koble
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 1}],
                   godkjent=False)
    r = _post(klient, tok, f"/v1/tilbud/{tid}/dom", {"status": "sendt"})
    assert r.status_code == 400, r.text
    import psycopg
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.Error) as ei:
            rt.execute("SELECT m26_tilbud_sendt(%s,%s,1,now(),'tilbud-v1',"
                       "'s****@x','test')", (TENANT, tid))
        # Dørens egen setning — ikke vaktens (den gjerder også, 169).
        assert "bare et godkjent tilbud kan bli sendt" in str(ei.value)
        rt.rollback()
    finally:
        rt.close()
    assert _tilstand(migrator, tid)[0][0] == "utkast"


@pg
def test_kvittering_for_et_annet_tilbud_bokfores_ikke(migrator, miljo, klient,
                                                       token):
    from api.tilbud import bokfor_tilbud_sendt
    from db.pg import koble
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tid, _ = _klar(klient, _btok(token), [{"produkt_id": str(kabel), "antall": 1}])
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        for kv, grunn in (
                ({"ressurs_id": "faktura:x"}, "ressurs_id_ikke_tilbud"),
                ({"ressurs_id": "tilbud:ikke-uuid"}, "kvittering_uten_gyldig_ressurs"),
                ({"ressurs_id": f"tilbud:{tid}"}, "oppdrag_ukjent")):
            ut = bokfor_tilbud_sendt(rt, TENANT, 990300, kv, "test")
            assert ut.get("avvik") == grunn, (kv, ut)
        rt.rollback()
    finally:
        rt.close()


@pg_plan
def test_kvittering_som_navngir_et_annet_tilbud_enn_oppdragets(
        migrator, miljo, app, klient, token):
    from api.tilbud import bokfor_tilbud_sendt
    from db.pg import koble
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 2}])
    oid = _bestilt_av_utloseren(app, tid)
    annet, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 3}])
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        ut = bokfor_tilbud_sendt(rt, TENANT, oid, {"ressurs_id": f"tilbud:{annet}",
                                                    "sendt_ts": "2026-09-10T10:00:00+00:00"},
                                 "test")
        rt.rollback()
    finally:
        rt.close()
    assert ut == {"avvik": "ressurs_avvik"}, ut
    assert _tilstand(migrator, tid)[0][0] == "godkjent"
    assert _tilstand(migrator, annet)[0][0] == "godkjent"


@pg_plan
def test_brudd_saken_kan_bli_et_komplett_oppdrag(migrator, miljo, app, klient,
                                                  token):
    from db import kryptering
    from m37 import reparasjoner
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg(rabatt=100)
    adresse = "per-" + secrets.token_hex(3) + "@nordvik.example"
    tid, _ = _klar(klient, _btok(token), [{"produkt_id": str(kabel), "antall": 10,
                                            "enhetspris_ore": 1100}],
                   kunde_epost=adresse)
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["tilbud"] == tid]
    assert mine and mine[0]["utfall"] == "brudd", res
    sak = mine[0]["unntak_id"]
    _sett_kontekst(migrator, TENANT)
    ct, key_id, nonce = migrator.execute(
        "SELECT payload_kryptert, key_id, nonce FROM unntak"
        " WHERE tenant=%s AND id=%s", (TENANT, sak)).fetchone()
    nok = migrator.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND key_id=%s",
        (TENANT, key_id)).fetchone()[0]
    migrator.rollback()
    dek = kryptering._pakk_ut((key_id, nok), TENANT)[1]
    payload = kryptering.dekrypter(dek, bytes(ct), bytes(nonce), TENANT, key_id)
    assert payload["handling"] == "tilbud.generer"
    assert payload["tilbud_id"] == tid and payload["omfang"] == "tilbud"
    assert payload["sum_ore"] == 11000
    assert adresse not in str(payload)
    plan = reparasjoner._r1_reinnsending(payload, None)
    assert plan.utfall == "oppdrag", (plan.utfall, plan.grunn)
    assert plan.oppdragstype == "tilbud.generer"
    assert set(plan.reparasjonsinput) == {"tilbud_id", "sum_ore", "gyldig_til",
                                          "omfang"}
