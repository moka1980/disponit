"""Porten for ARC B tilbud, PR 6: flaten og bevisgrensen.

  1. `GET /v1/tilbud` bærer per tilbud hva plattformens arm gjorde (174):
     `bestilling` (utfall, oppdrag eller sak, tidspunkt) og `sending`
     (tidspunkt, oppdrag, mal) — null der ingenting har skjedd. Statusen
     `sendt` står i lista og i sammendraget; avsenderprofilen bæres.
     Aldri adressen.
  2. Bevisgrensen `m26-tilbud-v1` er registrert med ti punkter i parformen,
     og hvert punkt har en NAVNGITT port i ARC B-testene.
  3. Artefaktskjemaet er generert fra invariantene.

Punkt → port: se PUNKT_PORTER (eksplisitt binding, M-14-formen).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_tilbud_port import _btok, _klar, _tilbudpolicy
from .test_m17_avsender_port import _post
from .test_m26_sending_port import (MODUL, _Sender, _bestilt_av_utloseren,
                                    _kjor_til, _release, _signer)
from .test_m26_tilbud_port import _rigg
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")

#: Punkt → (testmodul, testfunksjon). Porten under måler at hver finnes.
PUNKT_PORTER = {
    "tilbud_uten_policy": [
        ("test_m26_tilbudsutloser_port",
         "test_policy_uten_handlingen_bestiller_ingenting")],
    "tilbud_uten_godkjenning": [
        ("test_m26_tilbudsutloser_port",
         "test_kandidatdora_krever_godkjent_gyldig_med_linjer"),
        ("test_bestilling_tilbud_port", "test_maalportene_stopper_foer_kvote"),
        ("test_m26_tilbud_port", "test_dommen_gaar_bare_fra_utkast"),
        ("test_m26_bokforing_port", "test_sendt_er_aldri_en_dom")],
    "priser_utenfor_boka": [
        ("test_bestilling_tilbud_port",
         "test_en_pris_under_rabattgrensen_er_en_usann_attestasjon"),
        ("test_m26_tilbudsutloser_port",
         "test_rabatt_under_grensen_gir_brudd_en_gang"),
        ("test_m26_bokforing_port",
         "test_brudd_saken_kan_bli_et_komplett_oppdrag")],
    "klausul_erstattet": [
        ("test_bestilling_tilbud_port",
         "test_en_erstattet_klausul_er_en_usann_attestasjon"),
        ("test_m26_tilbud_port", "test_faktaene_regnes_av_registeret")],
    "belop_over_policyens_tak": [
        ("test_bestilling_tilbud_port",
         "test_summen_over_policyens_tak_stopper")],
    "dobbel_bestilling_samme_tilbud": [
        ("test_m26_tilbudsutloser_port",
         "test_en_runde_bestiller_og_bokforer_og_runde_to_gjor_ingenting")],
    "dobbel_sending_samme_oppdrag": [
        ("test_m26_bokforing_port", "test_kvitteringen_setter_sendt_en_gang"),
        ("test_m26_controller",
         "test_gyldig_claim_sender_en_gang_og_kvitterer_utfort"),
        ("test_m26_controller", "test_ukvittert_og_utlopt_frist")],
    "utlopt_eller_uten_linjer": [
        ("test_bestilling_tilbud_port", "test_maalportene_stopper_foer_kvote"),
        ("test_m26_sending_port",
         "test_utlopt_mellom_bestilling_og_claim_sender_ikke"),
        ("test_m26_controller", "test_hindring_gir_feilet_uten_sending")],
    "kvittering_uten_bokforing": [
        ("test_m26_sending_port", "test_tilbudet_gaar_ut_hele_veien"),
        ("test_m26_bokforing_port",
         "test_kvittering_for_et_annet_tilbud_bokfores_ikke"),
        ("test_m26_bokforing_port",
         "test_kvittering_som_navngir_et_annet_tilbud_enn_oppdragets")],
    "kill_switch_konsumerte_tilbud": [
        ("test_m26_tilbudsutloser_port", "test_kill_switch_stopper_utloseren")],
}


@pg_plan
def test_flaten_baerer_plattformens_arm_som_tekst(migrator, miljo, app, klient,
                                                   token):
    from modules.m26_prisbok import controller
    controller._sov = lambda s: None
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    adresse = "styret-" + secrets.token_hex(3) + "@nordvik.example"
    r = _post(klient, tok, "/v1/tilbud/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "post@fjordlys.example"})
    assert r.status_code == 200, r.text
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 4}],
                   kunde_epost=adresse)
    urort, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 1}],
                     godkjent=False)
    oid = _bestilt_av_utloseren(app, tid)
    hode = {"authorization": f"Bearer {tok}"}
    r = klient.get("/v1/tilbud", headers=hode)
    assert r.status_code == 200, r.text
    svar = r.json()
    assert svar["avsenderprofil"]["avsender_navn"] == "Fjordlys Elektro AS"
    rad = {x["tilbud_id"]: x for x in svar["tilbud"]}
    b = rad[tid]["bestilling"]
    assert b["utfall"] == "tillat" and b["oppdrag_id"] == oid \
        and b["unntak_id"] is None and b["bestilt_ts"], b
    assert rad[tid]["sending"] is None
    assert rad[urort]["bestilling"] is None and rad[urort]["sending"] is None
    assert adresse not in r.text
    mtk, _ = _onboard_token(klient, migrator, MODUL, _release(migrator))
    ut = _kjor_til(klient, mtk, _Sender(), _signer, tid)
    assert ut["utfall"] == "utfort", ut
    r = klient.get("/v1/tilbud", headers=hode)
    svar = r.json()
    x = {y["tilbud_id"]: y for y in svar["tilbud"]}[tid]
    assert x["status"] == "sendt"
    assert x["sending"]["oppdrag_id"] == oid \
        and x["sending"]["malversjon"] == "tilbud-v1" \
        and x["sending"]["sendt_ts"], x
    assert svar["sammendrag"]["sendte"] >= 1
    assert adresse not in r.text
    r = klient.get(f"/v1/tilbud/{tid}", headers=hode)
    assert r.status_code == 200 and r.json()["sending"]["oppdrag_id"] == oid
    assert r.json()["bestilling"]["oppdrag_id"] == oid
    assert adresse not in r.text


def test_bevisgrensen_har_ti_punkter_med_navngitte_porter():
    import importlib
    from manifestskjema import (KRAVGRENSER, M26_TILBUD_INVARIANTER,
                                _sjekk_grenser)
    g = KRAVGRENSER["m26-tilbud-v1"]
    assert len(M26_TILBUD_INVARIANTER) == \
        len(set(M26_TILBUD_INVARIANTER)) == 10
    assert g["invarianter"] is M26_TILBUD_INVARIANTER
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["krav_ja"] == ("rundtur_paa_disponit_com",)
    for inv in M26_TILBUD_INVARIANTER:
        assert inv in PUNKT_PORTER, f"punktet {inv} har ingen navngitt port"
        for modul, navn in PUNKT_PORTER[inv]:
            m = importlib.import_module(f"tests.{modul}")
            assert callable(getattr(m, navn, None)), (inv, modul, navn)
    assert set(PUNKT_PORTER) == set(M26_TILBUD_INVARIANTER)

    def art(**over):
        m = {f"{n}_forsok": 1 for n in M26_TILBUD_INVARIANTER}
        m |= {f"{n}_brudd": 0 for n in M26_TILBUD_INVARIANTER}
        m["rundtur_paa_disponit_com"] = True
        m.update(over)
        return {"krav_id": "m26-tilbud-v1", "bestatt": True, "maalt": m}
    assert _sjekk_grenser("m26-tilbud-v1", art()) == []
    assert _sjekk_grenser("m26-tilbud-v1", art(kvittering_uten_bokforing_brudd=1))
    assert _sjekk_grenser("m26-tilbud-v1",
                          art(dobbel_sending_samme_oppdrag_forsok=0))
    assert _sjekk_grenser("m26-tilbud-v1", art(rundtur_paa_disponit_com="ja"))


def test_artefaktskjemaet_er_generert_fra_invariantene():
    import json
    from pathlib import Path
    from manifestskjema import (ARTEFAKTSKJEMAER, M26_TILBUD_INVARIANTER,
                                valider_artefaktformat)
    rot = Path(__file__).resolve().parents[1]
    sk = json.loads((rot / ARTEFAKTSKJEMAER["m26-tilbud-v1"])
                    .read_text(encoding="utf-8"))
    felt = set(sk["properties"]["maalt"]["required"])
    assert felt == ({f"{n}_forsok" for n in M26_TILBUD_INVARIANTER}
                    | {f"{n}_brudd" for n in M26_TILBUD_INVARIANTER}
                    | {"rundtur_paa_disponit_com"})
    assert set(sk["properties"]["maalt"]["properties"]) == felt
    assert sk["properties"]["oppsett"]["properties"]["modul"] == {
        "const": "m26_prisbok"}
    m = {f"{n}_forsok": 1 for n in M26_TILBUD_INVARIANTER}
    m |= {f"{n}_brudd": 0 for n in M26_TILBUD_INVARIANTER}
    m["rundtur_paa_disponit_com"] = False
    art = {"krav_id": "m26-tilbud-v1", "ts": "2026-09-10T12:00:00Z",
           "bestatt": False, "oppsett": {"modul": "m26_prisbok",
                                         "commit": "a" * 40, "vert": "v",
                                         "tenant": "t"},
           "maalt": m, "funn": []}
    assert valider_artefaktformat(art, "m26-tilbud-v1") == []
    assert valider_artefaktformat({**art, "maalt": {**m, "x": 1}},
                                  "m26-tilbud-v1")
