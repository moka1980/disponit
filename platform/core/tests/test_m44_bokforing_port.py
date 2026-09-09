"""Porten for ARC B kampanje, PR 5: kvitteringen når registeret — og
saken bærer nok til å løses.

  1. Hele veien: levering → signert `utfort`-kvittering → raden i
     `kampanjelevering` med oppdrag, tidspunkt, malversjon og maske —
     aldri adressen; evidensen `kampanje.levert` i revisjonsloggen. Samme
     kvittering én gang til er idempotent: én rad, én evidens.
  2. En brudd-sak fra utløseren (samtykke trukket etter planleggingen)
     bærer kampanjens referanser i sakspayloaden — og ingen adresse, ingen
     tekst — og M-37s R1 planlegger et KOMPLETT `kampanje.send`-oppdrag
     av den, ikke `manuell`.
  3. En kvittering for et annet par / uten gyldig ressurs aksepteres,
     men bokføres ikke (avvik i driftsloggen, oppdraget likevel `utfort`).
  4. Lesedøra `m44_leveringene` gir flaten leveringene per kampanje.

MUTASJONER SOM DREPER DENNE: fjern kroken i `_ingest_kvittering` (1),
sløyf ressurssammenligningen i `bokfor_kampanje_levert` (3), eller ta
`PER_HANDLING["kampanje.send"]` ut av minimeringen (2).
"""
import secrets
import uuid

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_kampanje_port import I_DAG, _kampanjepolicy, _klar
from .test_m37 import _sett_kontekst, _signer_kvittering
from .test_m44_kampanje import _rt, _samtykke
from .test_m44_kampanjeutloser_port import _pa, _runde
from .test_m44_kontakt_port import _post
from .test_m44_sending_port import (_Sender, _bestilt_av_utloseren,
                                    _kjor_til, _klar_med_ekte_adresse,
                                    _oppdrag, _release)
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _leveringene(migrator, kid):
    """Lesedøra som RUNTIME (den er grantet til `disponit`, ikke
    migrator) + evidensen i loggen."""
    from db.pg import koble
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        rader = rt.execute(
            "SELECT mottaker_id::text, oppdrag_id, malversjon, mottaker_maske"
            " FROM m44_leveringene(%s,%s)", (TENANT, kid)).fetchall()
        rt.rollback()
    finally:
        rt.close()
    # Evidensen bærer detaljene i hashen (114 `m44_evidens`), ikke i
    # begrunnelsen — den telles per handling, og testene måler ØKNINGEN.
    _sett_kontekst(migrator, TENANT)
    ev = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m44_kampanje' AND handling='kampanje.levert'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    return rader, ev


@pg_plan
def test_kvitteringen_bokforer_leveringen_en_gang(migrator, miljo, app,
                                                  klient, token):
    from modules.m44_kampanje import controller
    controller._sov = lambda s: None
    _kampanjepolicy(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    adresse = "kari-" + secrets.token_hex(3) + "@example.com"
    kid, mid = _klar_med_ekte_adresse(klient, tok, adresse)
    oid = _bestilt_av_utloseren(app, kid)
    _, ev_for = _leveringene(migrator, kid)
    assert _leveringene(migrator, kid)[0] == []
    mtk, _ = _onboard_token(klient, migrator, "m44_kampanje",
                            _release(migrator))
    kvitteringer = []

    def signer(kropp):
        signert = _signer_kvittering(kropp, verifikator="v_samtykke")
        kvitteringer.append(signert)
        return signert
    ut = _kjor_til(klient, mtk, _Sender(), signer, kid)
    assert ut["utfall"] == "utfort", ut
    rader, ev = _leveringene(migrator, kid)
    assert rader == [(str(mid), oid, "kampanje-v1", "k****@example.com")], \
        rader
    assert ev == ev_for + 1
    assert adresse not in str(rader)
    # Samme signerte kvittering én gang til: idempotent på plattformen,
    # og registeret får ingen andre rad.
    r = klient.post("/v1/oppdrag/kvittering", json=kvitteringer[-1],
                    headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 200, r.text
    assert _leveringene(migrator, kid) == (rader, ev_for + 1)
    assert _oppdrag(migrator, oid)[0] == "utfort"


@pg_plan
def test_brudd_saken_kan_bli_et_komplett_oppdrag(migrator, miljo, app,
                                                  klient, token):
    """Samtykket trekkes etter planleggingen: utløseren bestiller,
    policyen gir brudd → sak. Sakens payload bærer kampanjens
    referanser (og verken adresse eller tekst), og R1 planlegger et
    komplett oppdrag av den."""
    from db import kryptering
    from m37 import reparasjoner
    _kampanjepolicy(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    adresse = "per-" + secrets.token_hex(3) + "@example.com"
    kid, mid = _klar_med_ekte_adresse(klient, tok, adresse)
    c = _rt()
    try:
        _samtykke(c, TENANT, mid, "trukket", I_DAG)
    finally:
        c.close()
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["kampanje"] == str(kid)]
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
    assert payload["handling"] == "kampanje.send"
    assert payload["kampanje_id"] == str(kid)
    assert payload["mottaker_id"] == str(mid)
    assert payload["planlagt_sendt"] == I_DAG
    assert payload["omfang"] == "mottaker"
    assert adresse not in str(payload) and "høstsjekk" not in str(payload)
    assert not {k for k in payload if "epost" in k or "kontakt" in k}
    plan = reparasjoner._r1_reinnsending(payload, None)
    assert plan.utfall == "oppdrag", (plan.utfall, plan.grunn)
    assert plan.oppdragstype == "kampanje.send"
    assert set(plan.reparasjonsinput) == {
        "kampanje_id", "mottaker_id", "planlagt_sendt", "omfang"}


def test_minimeringen_slipper_kampanjens_felter_bare_for_kampanje():
    from api.minimering import minimer_payload
    ev = {"handling": "kampanje.send", "ressurs_id": "kampanje:x:y",
          "valuta": "NOK", "kampanje_id": str(uuid.uuid4()),
          "mottaker_id": str(uuid.uuid4()), "planlagt_sendt": "2026-09-09",
          "omfang": "mottaker", "mottaker_epost": "a@b.no",
          "tekst": "Hei"}
    ut = minimer_payload(ev, "manglende_data", ["attestasjon_negativ"])
    assert ut["kampanje_id"] == ev["kampanje_id"]
    assert ut["mottaker_id"] == ev["mottaker_id"]
    assert "mottaker_epost" not in ut and "tekst" not in ut
    ut2 = minimer_payload({**ev, "handling": "faktura.bokfor"},
                          "manglende_data", [])
    assert "kampanje_id" not in ut2 and "mottaker_id" not in ut2


@pg
def test_kvittering_for_et_annet_par_bokfores_ikke(migrator, miljo):
    from api.kampanje import bokfor_kampanje_levert
    from db.pg import koble
    _kampanjepolicy(migrator)
    kid, mid = _klar()
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        for kv, grunn in (
                ({"ressurs_id": "fordring:x"}, "ressurs_id_ikke_kampanje"),
                ({"ressurs_id": "kampanje:x:y"},
                 "kvittering_uten_gyldig_ressurs"),
                ({"ressurs_id": f"kampanje:{kid}:{mid}"}, "oppdrag_ukjent")):
            ut = bokfor_kampanje_levert(rt, TENANT, 990100, kv, "test")
            assert ut.get("avvik") == grunn, (kv, ut)
        rt.rollback()
    finally:
        rt.close()


@pg_plan
def test_kvittering_som_navngir_et_annet_par_enn_oppdragets(
        migrator, miljo, app, klient, token):
    """Modulen signerer, men peker på en ANNEN mottaker enn oppdragets:
    kvitteringen aksepteres (e-posten er ute), men registeret får ingen
    rad for det andre paret — og heller ikke for oppdragets."""
    from api.kampanje import bokfor_kampanje_levert
    from db.pg import koble
    _kampanjepolicy(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    kid, mid = _klar_med_ekte_adresse(
        klient, tok, "a-" + secrets.token_hex(3) + "@example.com")
    oid = _bestilt_av_utloseren(app, kid)
    annen_kid, annen_mid = _klar()
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        ut = bokfor_kampanje_levert(
            rt, TENANT, oid, {"ressurs_id": f"kampanje:{annen_kid}:{annen_mid}",
                              "sendt_ts": "2026-09-09T10:00:00+00:00"},
            "test")
        rt.rollback()
    finally:
        rt.close()
    assert ut == {"avvik": "ressurs_avvik"}, ut
    assert _leveringene(migrator, kid)[0] == []
    assert _leveringene(migrator, annen_kid)[0] == []
