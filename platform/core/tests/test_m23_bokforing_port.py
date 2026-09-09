"""Porten for ARC B, PR 5: kvitteringen når registeret — og saken
bærer nok til å løses.

  1. Hele veien: sending → signert `utfort`-kvittering → fordringen står
     på trinnet som ble purret, hendelsen `purring` står i fordringens
     historikk, evidensen `purring.sendt` i revisjonsloggen. Samme
     kvittering én gang til er idempotent: én hendelse.
  2. Døra alene: et trinn som ikke er fordringens neste bokføres som
     `purring.sendt_utenfor_rekkefolge` — hendelsen føres (e-posten er
     ute), trinnet står. Gjenspill gir `bokfort=false`.
  3. En brudd-sak fra utløseren (5 døgn, policy krever 14) bærer
     purringens referanser i sakspayloaden, og M-37s R1 planlegger et
     KOMPLETT `purring.send`-oppdrag av den — ikke `manuell`.
  4. En kvittering uten gyldig trinn/ressurs aksepteres, men bokføres
     ikke (avvik i driftsloggen, oppdraget likevel `utfort`).

MUTASJONER SOM DREPER DENNE: fjern kroken i `_ingest_kvittering` (1),
la døra flytte trinnet uansett rekkefølge (2), eller ta
`PER_HANDLING["purring.send"]` ut av minimeringen (3).
"""
import json
import secrets
import uuid

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m23_purringsutloser_port import _bransjemal, _pa, _runde
from .test_m23_sending_port import (_Sender, _fordring_med_adresse,
                                    _oppdrag, _release)
from .test_m37 import _sett_kontekst, _signer_kvittering
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _fordring_tilstand(migrator, fid):
    _sett_kontekst(migrator, TENANT)
    trinn = migrator.execute(
        "SELECT trinn FROM fordring WHERE tenant=%s AND fordring_id=%s",
        (TENANT, fid)).fetchone()[0]
    hendelser = migrator.execute(
        "SELECT art, trinn FROM fordringshendelse WHERE tenant=%s"
        " AND fordring_id=%s AND art='purring' ORDER BY opprettet",
        (TENANT, fid)).fetchall()
    evidens = migrator.execute(
        "SELECT handling FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m23_fordring' AND handling LIKE 'purring.sendt%%'"
        " AND begrunnelse::text LIKE %s ORDER BY id",
        (TENANT, "%" + str(fid)[:8] + "%")).fetchall()
    migrator.rollback()
    return trinn, hendelser, evidens


@pg_plan
def test_kvitteringen_flytter_trinnet_og_forer_hendelsen(
        migrator, miljo, app, klient, token):
    from modules.m23_fordring import controller
    controller._sov = lambda s: None
    _bransjemal(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    fid = _fordring_med_adresse(klient, tok,
                                "k-" + secrets.token_hex(3) + "@n.example")
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["fordring"] == str(fid)]
    assert mine and mine[0]["utfall"] == "tillat", res
    oid = mine[0]["oppdrag_id"]
    assert _fordring_tilstand(migrator, fid)[0] == 0
    mtk, _ = _onboard_token(klient, migrator, "m23_fordring",
                            _release(migrator))
    kvitteringer = []

    def signer(kropp):
        signert = _signer_kvittering(kropp)
        kvitteringer.append(signert)
        return signert
    ut = controller.kjor_en(klient, mtk, _Sender(), signer)
    assert ut["utfall"] == "utfort", ut
    trinn, hendelser, _ = _fordring_tilstand(migrator, fid)
    assert trinn == 1, trinn
    assert hendelser == [("purring", 1)], hendelser
    _sett_kontekst(migrator, TENANT)
    ev = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m23_fordring' AND handling='purring.sendt'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert ev >= 1
    # Samme signerte kvittering én gang til: idempotent på plattformen, og
    # registeret får ingen andre hendelse.
    r = klient.post("/v1/oppdrag/kvittering", json=kvitteringer[-1],
                    headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 200, r.text
    trinn, hendelser, _ = _fordring_tilstand(migrator, fid)
    assert trinn == 1 and hendelser == [("purring", 1)], hendelser
    assert _oppdrag(migrator, oid)[0] == "utfort"


@pg
def test_dora_bokforer_utenfor_rekkefolge_uten_a_flytte(migrator, miljo):
    from .test_m23_fordring import _fordring, _plan, _rt
    c = _rt()
    try:
        _plan(c, TENANT)
        fid = _fordring(c, TENANT, forfall_siden=20)
        _sett_kontekst(c, TENANT)
        rad = c.execute(
            "SELECT bokfort, flyttet, trinn_naa FROM m23_purring_sendt("
            "%s,%s,%s,%s,now(),%s,%s,%s)",
            (TENANT, fid, 3, 990001, "purring-v1", "k****@n.example",
             "test")).fetchone()
        c.commit()
        assert rad == (True, False, 0), rad
        # Gjenspill av samme oppdrag: ingen ny hendelse.
        _sett_kontekst(c, TENANT)               # SET LOCAL døde med commit
        rad = c.execute(
            "SELECT bokfort, flyttet, trinn_naa FROM m23_purring_sendt("
            "%s,%s,%s,%s,now(),%s,%s,%s)",
            (TENANT, fid, 3, 990001, "purring-v1", "k****@n.example",
             "test")).fetchone()
        c.commit()
        assert rad == (False, False, 0), rad
        # Riktig neste trinn flytter.
        _sett_kontekst(c, TENANT)
        rad = c.execute(
            "SELECT bokfort, flyttet, trinn_naa FROM m23_purring_sendt("
            "%s,%s,%s,%s,now(),%s,%s,%s)",
            (TENANT, fid, 1, 990002, "paaminnelse-v1", "k****@n.example",
             "test")).fetchone()
        c.commit()
        assert rad == (True, True, 1), rad
    finally:
        c.close()
    trinn, hendelser, _ = _fordring_tilstand(migrator, fid)
    assert trinn == 1 and hendelser == [("purring", 3), ("purring", 1)]
    _sett_kontekst(migrator, TENANT)
    arter = [r[0] for r in migrator.execute(
        "SELECT handling FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m23_fordring' AND handling LIKE 'purring.sendt%%'"
        " ORDER BY id DESC LIMIT 2", (TENANT,)).fetchall()]
    migrator.rollback()
    assert sorted(arter) == ["purring.sendt",
                             "purring.sendt_utenfor_rekkefolge"], arter


@pg_plan
def test_brudd_saken_kan_bli_et_komplett_oppdrag(migrator, miljo, app,
                                                  klient, token):
    """Utløseren bestiller 5 døgn over forfall; policyen krever 14 →
    brudd → sak. Sakens payload bærer purringens referanser, og R1
    planlegger et komplett oppdrag av den."""
    from db import kryptering
    from m37 import reparasjoner
    _bransjemal(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    fid = _fordring_med_adresse(klient, tok,
                                "k-" + secrets.token_hex(3) + "@n.example",
                                forfall_siden=5)
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["fordring"] == str(fid)]
    assert mine and mine[0]["utfall"] == "brudd", res
    uid = mine[0]["unntak_id"]
    _sett_kontekst(migrator, TENANT)
    ct, key_id, nonce = migrator.execute(
        "SELECT payload_kryptert, key_id, nonce FROM unntak"
        " WHERE tenant=%s AND id=%s", (TENANT, uid)).fetchone()
    nok = migrator.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND key_id=%s",
        (TENANT, key_id)).fetchone()[0]
    migrator.rollback()
    dek = kryptering._pakk_ut((key_id, nok), TENANT)[1]
    payload = kryptering.dekrypter(dek, bytes(ct), bytes(nonce), TENANT,
                                   key_id)
    assert payload["handling"] == "purring.send"
    assert payload["fordring_id"] == str(fid)
    assert payload["trinn"] == 1 and payload["handling_trinn"] == "paaminnelse"
    assert payload["rest_ore"] == 250000 and payload["omfang"] == "trinn"
    assert payload["fakturanummer"].startswith("F-")
    # …og ingen adresse, i noen form.
    assert not {k for k in payload if "mottaker" in k or "epost" in k}
    plan = reparasjoner._r1_reinnsending(payload, None)
    assert plan.utfall == "oppdrag", (plan.utfall, plan.grunn)
    assert plan.oppdragstype == "purring.send"
    assert plan.reparasjonsinput["fordring_id"] == str(fid)
    assert set(plan.reparasjonsinput) == {
        "fordring_id", "fakturanummer", "trinn", "handling_trinn",
        "rest_ore", "omfang"}


@pg_plan
def test_kvittering_for_en_annen_fordring_bokfores_ikke(
        migrator, miljo, app, klient, token):
    """CodeRabbit på PR 5: kvitteringen er modulens ord — fordringen den
    navngir må være OPPDRAGETS. Ellers kunne én sending flytte trinnet
    på en annen fordring."""
    from api.fordring import bokfor_purring_sendt
    from db.pg import koble
    from .test_m23_fordring import _fordring, _rt
    _bransjemal(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    fid = _fordring_med_adresse(klient, tok,
                                "k-" + secrets.token_hex(3) + "@n.example")
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    oid = [x for x in res["resultater"]
           if x["fordring"] == str(fid)][0]["oppdrag_id"]
    c = _rt()
    try:
        annen = _fordring(c, TENANT, forfall_siden=20)
    finally:
        c.close()
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        ut = bokfor_purring_sendt(rt, TENANT, oid,
                                  {"ressurs_id": f"fordring:{annen}",
                                   "trinn": 1}, "test")
        assert ut == {"avvik": "ressurs_avvik"}, ut
        ut = bokfor_purring_sendt(rt, TENANT, oid,
                                  {"ressurs_id": f"fordring:{fid}",
                                   "trinn": 2}, "test")
        assert ut == {"avvik": "trinn_avvik"}, ut
        ut = bokfor_purring_sendt(rt, TENANT, 990900,
                                  {"ressurs_id": f"fordring:{fid}",
                                   "trinn": 1}, "test")
        assert ut == {"avvik": "oppdrag_ukjent"}, ut
        rt.rollback()
        assert _fordring_tilstand(migrator, fid)[0] == 0
        # Riktig fordring og trinn (og et uleselig sendt_ts, som bare
        # blir «nå»): bokført og flyttet.
        _sett_kontekst(rt, TENANT)
        ut = bokfor_purring_sendt(rt, TENANT, oid,
                                  {"ressurs_id": f"fordring:{fid}",
                                   "trinn": 1, "sendt_ts": "ugyldig"},
                                  "test")
        rt.commit()
        assert ut == {"bokfort": True, "flyttet": True, "trinn_naa": 1}, ut
    finally:
        rt.close()
    assert _fordring_tilstand(migrator, annen)[0] == 0
    assert _fordring_tilstand(migrator, fid)[0] == 1


def test_minimeringen_slipper_purringens_felter_bare_for_purring():
    from api.minimering import minimer_payload
    ev = {"handling": "purring.send", "ressurs_id": "fordring:x",
          "valuta": "NOK", "fordring_id": str(uuid.uuid4()),
          "fakturanummer": "F-1", "trinn": 1, "handling_trinn": "purring",
          "rest_ore": 100, "omfang": "trinn", "mottaker_epost": "a@b.no"}
    ut = minimer_payload(ev, "manglende_data", ["attestasjon_mangler"])
    assert ut["fordring_id"] == ev["fordring_id"] and ut["rest_ore"] == 100
    assert "mottaker_epost" not in ut
    ut2 = minimer_payload({**ev, "handling": "faktura.bokfor"},
                          "manglende_data", [])
    assert "fordring_id" not in ut2 and "rest_ore" not in ut2


@pg
def test_kvittering_uten_gyldig_trinn_aksepteres_men_bokfores_ikke(
        migrator, miljo):
    from api.fordring import bokfor_purring_sendt
    from db.pg import koble
    from .test_m23_fordring import _fordring, _plan, _rt
    c = _rt()
    try:
        _plan(c, TENANT)
        fid = _fordring(c, TENANT, forfall_siden=20)
    finally:
        c.close()
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        for kv, grunn in (
                ({"ressurs_id": "inndata:x", "trinn": 1},
                 "ressurs_id_ikke_fordring"),
                ({"ressurs_id": f"fordring:{fid}", "trinn": "x"},
                 "kvittering_uten_gyldig_trinn"),
                ({"ressurs_id": f"fordring:{fid}"},
                 "kvittering_uten_gyldig_trinn"),
                ({"ressurs_id": f"fordring:{uuid.uuid4()}", "trinn": 1},
                 "oppdrag_ukjent")):
            ut = bokfor_purring_sendt(rt, TENANT, 990100, kv, "test")
            assert ut.get("avvik") == grunn, (kv, ut)
        rt.rollback()
    finally:
        rt.close()
    assert _fordring_tilstand(migrator, fid)[0] == 0
