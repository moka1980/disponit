"""Porten for ARC B kundeservice, PR 5: kvitteringen når registeret — og
saken bærer nok til å løses.

  1. Hele veien: sending → signert `utfort`-kvittering → utkastet er
     `sendt`, henvendelsen lukket som «besvart» med kvitteringens aktør,
     evidensen `svar.sendt` uten tekst og adresse. Samme kvittering én
     gang til er idempotent: én evidens, ingen ny lukking.
  2. `sendt` er aldri en dom: `POST …/dom {sendt}` → 400, og døra
     `m17_svar_sendt` nekter et utkast som ikke er godkjent.
  3. En brudd-sak fra utløseren (ikke godkjent → attestasjon_negativ)
     bærer referansene i sakspayloaden — verken adresse eller tekst —
     og M-37s R1 planlegger et KOMPLETT oppdrag av den.
  4. En kvittering for et annet par / uten gyldig ressurs aksepteres,
     men bokføres ikke.

MUTASJONER SOM DREPER DENNE: fjern kroken i `_ingest_kvittering` (1),
sløyf ressurssammenligningen i `bokfor_svar_sendt` (4), eller ta
`PER_HANDLING["kundeservice.svar.send"]` ut av minimeringen (3).
"""
import secrets
import uuid

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_svar_port import _klar, _svarpolicy, _tok
from .test_m17_avsender_port import _post
from .test_m17_sending_port import (_Sender, _bestilt_av_utloseren, _kjor_til,
                                    _oppdrag, _release, _signer)
from .test_m17_svarutloser_port import _pa, _runde
from .test_m37 import _sett_kontekst, _signer_kvittering
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _tilstand(migrator, hid, uid):
    _sett_kontekst(migrator, TENANT)
    u = migrator.execute(
        "SELECT status FROM svarutkast WHERE tenant=%s AND utkast_id=%s",
        (TENANT, uid)).fetchone()[0]
    h = migrator.execute(
        "SELECT lukket_utfall, lukket_av FROM henvendelse"
        " WHERE tenant=%s AND henvendelse_id=%s", (TENANT, hid)).fetchone()
    ev = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m17_kundeservice' AND handling='svar.sendt'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    return u, h, ev


@pg_plan
def test_kvitteringen_setter_sendt_og_lukker_besvart_en_gang(
        migrator, miljo, app, klient, token):
    from modules.m17_kundeservice import controller
    controller._sov = lambda s: None
    _svarpolicy(migrator)
    tok = _tok(token)
    adresse = "kari-" + secrets.token_hex(3) + "@nordvik.example"
    hid, uid = _klar(klient, tok, adresse=adresse)
    oid = _bestilt_av_utloseren(app, hid)
    _, _, ev_for = _tilstand(migrator, hid, uid)
    mtk, _ = _onboard_token(klient, migrator, "m17_kundeservice",
                            _release(migrator))
    kvitteringer = []

    def signer(kropp):
        signert = _signer(kropp)
        kvitteringer.append(signert)
        return signert
    ut = _kjor_til(klient, mtk, _Sender(), signer, hid)
    assert ut["utfall"] == "utfort", ut
    u, h, ev = _tilstand(migrator, hid, uid)
    assert u == "sendt" and h[0] == "besvart" and h[1].startswith("token:")
    assert ev == ev_for + 1
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
    assert _tilstand(migrator, hid, uid) == ("sendt", h, ev_for + 1)
    assert _oppdrag(migrator, oid)[0] == "utfort"
    # Køen viser henvendelsen ikke lenger (lukket).
    r = klient.get("/v1/kundeservice", headers={"authorization": f"Bearer {tok}"})
    assert hid not in [x["henvendelse_id"] for x in r.json()["koe"]]


@pg
def test_sendt_er_aldri_en_dom(migrator, miljo, klient, token):
    from db.pg import koble
    _svarpolicy(migrator)
    tok = _tok(token)
    hid, uid = _klar(klient, tok, godkjent=False)
    r = _post(klient, tok, f"/v1/kundeservice/utkast/{uid}/dom",
              {"status": "sendt"})
    assert r.status_code == 400, r.text
    import psycopg
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.Error) as ei:
            rt.execute("SELECT * FROM m17_svar_sendt(%s,%s,%s,1,now(),'svar-v1',"
                       "'k****@x','test')", (TENANT, hid, uid))
        assert "godkjent" in str(ei.value)
        rt.rollback()
    finally:
        rt.close()
    assert _tilstand(migrator, hid, uid)[0] == "foreslatt"


@pg_plan
def test_brudd_saken_kan_bli_et_komplett_oppdrag(migrator, miljo, app,
                                                  klient, token):
    from db import kryptering
    from m37 import reparasjoner
    _svarpolicy(migrator)
    tok = _tok(token)
    adresse = "per-" + secrets.token_hex(3) + "@nordvik.example"
    # Godkjent med et fødselsnummer i teksten: DLP → brudd → sak.
    hid, uid = _klar(klient, tok, adresse=adresse,
                     tekst="Vi noterte fødselsnummer 010190 12345.")
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["henvendelse"] == hid]
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
    payload = kryptering.dekrypter(dek, bytes(ct), bytes(nonce), TENANT,
                                   key_id)
    assert payload["handling"] == "kundeservice.svar.send"
    assert payload["henvendelse_id"] == hid and payload["utkast_id"] == uid
    assert payload["omfang"] == "svar"
    assert adresse not in str(payload) and "010190" not in str(payload)
    assert not {k for k in payload if "epost" in k or "tekst" in k}
    plan = reparasjoner._r1_reinnsending(payload, None)
    assert plan.utfall == "oppdrag", (plan.utfall, plan.grunn)
    assert plan.oppdragstype == "kundeservice.svar.send"
    assert set(plan.reparasjonsinput) == {"henvendelse_id", "utkast_id",
                                          "omfang"}


def test_minimeringen_slipper_svarets_felter_bare_for_svar():
    from api.minimering import minimer_payload
    ev = {"handling": "kundeservice.svar.send", "ressurs_id": "henvendelse:x:y",
          "valuta": "NOK", "henvendelse_id": str(uuid.uuid4()),
          "utkast_id": str(uuid.uuid4()), "omfang": "svar",
          "mottaker_epost": "a@b.no", "tekst": "Hei"}
    ut = minimer_payload(ev, "manglende_data", ["attestasjon_negativ"])
    assert ut["henvendelse_id"] == ev["henvendelse_id"]
    assert "mottaker_epost" not in ut and "tekst" not in ut
    ut2 = minimer_payload({**ev, "handling": "faktura.bokfor"},
                          "manglende_data", [])
    assert "henvendelse_id" not in ut2


@pg
def test_kvittering_for_et_annet_par_bokfores_ikke(migrator, miljo, klient,
                                                   token):
    from api.kundeservice import bokfor_svar_sendt
    from db.pg import koble
    _svarpolicy(migrator)
    hid, uid = _klar(klient, _tok(token))
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        for kv, grunn in (
                ({"ressurs_id": "kampanje:x:y"}, "ressurs_id_ikke_henvendelse"),
                ({"ressurs_id": "henvendelse:x:y"},
                 "kvittering_uten_gyldig_ressurs"),
                ({"ressurs_id": f"henvendelse:{hid}:{uid}"}, "oppdrag_ukjent")):
            ut = bokfor_svar_sendt(rt, TENANT, 990100, kv, "test")
            assert ut.get("avvik") == grunn, (kv, ut)
        rt.rollback()
    finally:
        rt.close()


@pg_plan
def test_kvittering_som_navngir_et_annet_par_enn_oppdragets(
        migrator, miljo, app, klient, token):
    """Modulen signerer, men peker på et ANNET utkast enn oppdragets:
    kvitteringen aksepteres (svaret er ute), men registeret får verken
    `sendt` på det andre utkastet eller på oppdragets."""
    from api.kundeservice import bokfor_svar_sendt
    from db.pg import koble
    _svarpolicy(migrator)
    tok = _tok(token)
    hid, uid = _klar(klient, tok, adresse="a-" + secrets.token_hex(3)
                     + "@nordvik.example")
    oid = _bestilt_av_utloseren(app, hid)
    annen_hid, annet_uid = _klar(klient, tok)
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        ut = bokfor_svar_sendt(
            rt, TENANT, oid, {"ressurs_id": f"henvendelse:{annen_hid}:{annet_uid}",
                              "sendt_ts": "2026-09-09T10:00:00+00:00"}, "test")
        rt.rollback()
    finally:
        rt.close()
    assert ut == {"avvik": "ressurs_avvik"}, ut
    assert _tilstand(migrator, hid, uid)[0] == "godkjent"
    assert _tilstand(migrator, annen_hid, annet_uid)[0] == "godkjent"
