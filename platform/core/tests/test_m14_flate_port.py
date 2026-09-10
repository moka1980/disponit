"""Porten for ARC B bokføring, PR 5: flaten og bevisgrensen.

  1. `GET /v1/faktura` bærer per faktura hva plattformens arm gjorde:
     `bestilling` (handling, utfall, oppdrag eller sak, eller
     `menneske_kreves`) og `bokforing` (bilagsnummer, tidspunkt, oppdrag)
     — null der ingenting har skjedd. Statusen `bokfort` står i lista.
  2. Bevisgrensen `m14-bokforing-v1` er registrert med ti punkter i
     parformen, og hvert punkt har en NAVNGITT port i ARC B-testene.
  3. Artefaktskjemaet er generert fra invariantene.

Punkt → port:
  bokforing_uten_policy → test_m14_bokforingsutloser_port
  bokforing_med_kontrollavvik → test_m14_bokforingsutloser_port
      (kandidatdøra), test_bestilling_bokfor_port (mva-avvik / ukjent
      leverandør → sak), test_m14_bokforing_port (brudd-saken)
  bokforing_over_belopsgrense → test_bestilling_bokfor_port
      (belop_over_grense), test_m14_bokforingsutloser_port
      (menneske_kreves, velg_handling)
  dobbel_bestilling_samme_faktura → test_m14_bokforingsutloser_port
      (runde to)
  dobbel_bokforing_samme_oppdrag → test_m14_bokforing_port (gjenspill
      idempotent), test_m14_controller (ukvittert)
  bilag_avviker_fra_fakturaen → test_m14_bokforing_port (avskrift),
      test_m14_controller (utforelse_feil_*)
  bokforing_uten_manuell_kontroll_over_grensen →
      test_bestilling_bokfor_port (målportene),
      test_m14_bokforingsutloser_port (kandidatdøra)
  bokforing_av_avvist_eller_bokfort → test_bestilling_bokfor_port
      (409), test_m14_bilag_port (avvist mellom bestilling og claim),
      test_m14_bokforing_port (bokført er aldri en dom)
  kvittering_uten_bokforing → test_m14_bokforing_port
  kill_switch_konsumerte_fakturaer → test_m14_bokforingsutloser_port
"""
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_bokfor_port import _bokforpolicy, _fakt, _manuell, _rigg
from .test_m14_bilag_port import (MODUL, _bestilt_av_utloseren, _kjor_til,
                                  _release, _signer)
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")

#: Punkt → (testmodul, testfunksjon). Porten over måler at hver finnes.
PUNKT_PORTER = {
    "bokforing_uten_policy": [
        ("test_m14_bokforingsutloser_port",
         "test_policy_uten_handlingene_bestiller_ingenting")],
    "bokforing_med_kontrollavvik": [
        ("test_m14_bokforingsutloser_port",
         "test_kandidatdora_krever_rene_kontroller_og_manuell_over_grensen"),
        ("test_bestilling_bokfor_port",
         "test_mva_avvik_er_en_usann_attestasjon_og_en_sak"),
        ("test_bestilling_bokfor_port",
         "test_ukjent_leverandor_er_en_usann_attestasjon"),
        ("test_m14_bokforing_port",
         "test_brudd_saken_kan_bli_et_komplett_oppdrag")],
    "bokforing_over_belopsgrense": [
        ("test_bestilling_bokfor_port",
         "test_belopet_maales_av_policyen_ikke_av_bestilleren"),
        ("test_m14_bokforingsutloser_port",
         "test_belopet_velger_handlingen_og_over_taket_krever_et_menneske"),
        ("test_m14_bokforingsutloser_port",
         "test_velg_handling_er_policyens_grenser")],
    "dobbel_bestilling_samme_faktura": [
        ("test_m14_bokforingsutloser_port",
         "test_en_runde_bestiller_og_bokforer_og_runde_to_gjor_ingenting")],
    "dobbel_bokforing_samme_oppdrag": [
        ("test_m14_bokforing_port",
         "test_kvitteringen_bokforer_fakturaen_og_bilaget_en_gang"),
        ("test_m14_controller", "test_ukvittert_og_utlopt_frist")],
    "bilag_avviker_fra_fakturaen": [
        ("test_m14_bokforing_port",
         "test_bilaget_er_en_avskrift_et_annet_belop_bokfores_ikke"),
        ("test_m14_controller",
         "test_claim_svar_om_en_annen_faktura_bokfores_aldri")],
    "bokforing_uten_manuell_kontroll_over_grensen": [
        ("test_bestilling_bokfor_port", "test_maalportene_stopper_foer_kvote"),
        ("test_m14_bokforingsutloser_port",
         "test_kandidatdora_krever_rene_kontroller_og_manuell_over_grensen")],
    "bokforing_av_avvist_eller_bokfort": [
        ("test_bestilling_bokfor_port", "test_maalportene_stopper_foer_kvote"),
        ("test_m14_bilag_port",
         "test_avvist_mellom_bestilling_og_claim_bokfores_ikke"),
        ("test_m14_bokforing_port", "test_bokfort_er_aldri_en_dom")],
    "kvittering_uten_bokforing": [
        ("test_m14_bokforing_port",
         "test_kvitteringen_bokforer_fakturaen_og_bilaget_en_gang")],
    "kill_switch_konsumerte_fakturaer": [
        ("test_m14_bokforingsutloser_port", "test_kill_switch_stopper_utloseren")],
}
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


@pg_plan
def test_flaten_ser_bestillingen_og_bilaget(migrator, miljo, app, klient,
                                            token):
    from modules.m14_fakturakontroll import controller
    controller._sov = lambda s: None
    _bokforpolicy(migrator)
    _rigg(migrator)
    lesetok, _ = token(rolle="bestiller", scopes=("okonomi:read",))
    hode = {"authorization": f"Bearer {lesetok}"}
    ren = _fakt(migrator, netto=1000000, mva=250000, nummer="NK-2026-7001")
    over = _fakt(migrator, netto=12000000, mva=3000000)
    _manuell(migrator, over)
    urort = _fakt(migrator, netto=1100000, mva=220000)      # mva-avvik
    oid = _bestilt_av_utloseren(app, ren)
    r = klient.get("/v1/faktura", headers=hode)
    assert r.status_code == 200, r.text
    rad = {x["faktura_id"]: x for x in r.json()["fakturaer"]}
    b = rad[str(ren)]["bestilling"]
    assert b["handling"] == "faktura.bokfor" and b["utfall"] == "tillat" \
        and b["oppdrag_id"] == oid and b["unntak_id"] is None \
        and b["bestilt_ts"], b
    assert rad[str(ren)]["bokforing"] is None
    assert rad[str(over)]["bestilling"]["utfall"] == "menneske_kreves" \
        and rad[str(over)]["bestilling"]["handling"] == "ingen"
    assert rad[str(urort)]["bestilling"] is None \
        and rad[str(urort)]["bokforing"] is None
    mtk, _ = _onboard_token(klient, migrator, MODUL, _release(migrator))
    ut = _kjor_til(klient, mtk, _signer, ren)
    assert ut["utfall"] == "utfort", ut
    r = klient.get("/v1/faktura", headers=hode)
    f = {x["faktura_id"]: x for x in r.json()["fakturaer"]}[str(ren)]
    assert f["status"] == "bokfort"
    assert f["bokforing"]["bilagsnummer"] == "LF-NK-2026-7001" \
        and f["bokforing"]["oppdrag_id"] == oid \
        and f["bokforing"]["bokfort_ts"], f


def test_bevisgrensen_har_ti_punkter_med_navngitte_porter():
    from pathlib import Path
    from manifestskjema import (KRAVGRENSER, M14_BOKFORING_INVARIANTER,
                                _sjekk_grenser)
    g = KRAVGRENSER["m14-bokforing-v1"]
    assert len(M14_BOKFORING_INVARIANTER) == \
        len(set(M14_BOKFORING_INVARIANTER)) == 10
    assert g["invarianter"] is M14_BOKFORING_INVARIANTER
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["krav_ja"] == ("rundtur_paa_disponit_com",)
    # Bindingen er EKSPLISITT (CodeRabbit): hvert punkt peker på
    # testfunksjoner som må finnes — en slettet eller omdøpt port feller
    # denne, ikke bare et ord i en docstring.
    import importlib
    for inv in M14_BOKFORING_INVARIANTER:
        assert inv in PUNKT_PORTER, f"punktet {inv} har ingen navngitt port"
        for modul, navn in PUNKT_PORTER[inv]:
            m = importlib.import_module(f"tests.{modul}")
            assert callable(getattr(m, navn, None)), (inv, modul, navn)
    assert set(PUNKT_PORTER) == set(M14_BOKFORING_INVARIANTER)

    def art(**over):
        m = {f"{n}_forsok": 1 for n in M14_BOKFORING_INVARIANTER}
        m |= {f"{n}_brudd": 0 for n in M14_BOKFORING_INVARIANTER}
        m["rundtur_paa_disponit_com"] = True
        m.update(over)
        return {"krav_id": "m14-bokforing-v1", "bestatt": True, "maalt": m}
    assert _sjekk_grenser("m14-bokforing-v1", art()) == []
    assert _sjekk_grenser("m14-bokforing-v1",
                          art(kvittering_uten_bokforing_brudd=1))
    assert _sjekk_grenser("m14-bokforing-v1",
                          art(dobbel_bokforing_samme_oppdrag_forsok=0))
    assert _sjekk_grenser("m14-bokforing-v1",
                          art(rundtur_paa_disponit_com="ja"))


def test_artefaktskjemaet_er_generert_fra_invariantene():
    import json
    from pathlib import Path
    from manifestskjema import (ARTEFAKTSKJEMAER, M14_BOKFORING_INVARIANTER,
                                valider_artefaktformat)
    rot = Path(__file__).resolve().parents[1]
    sk = json.loads((rot / ARTEFAKTSKJEMAER["m14-bokforing-v1"])
                    .read_text(encoding="utf-8"))
    felt = set(sk["properties"]["maalt"]["required"])
    assert felt == ({f"{n}_forsok" for n in M14_BOKFORING_INVARIANTER}
                    | {f"{n}_brudd" for n in M14_BOKFORING_INVARIANTER}
                    | {"rundtur_paa_disponit_com"})
    assert sk["properties"]["oppsett"]["properties"]["modul"] == {
        "const": "m14_fakturakontroll"}
    m = {f"{n}_forsok": 1 for n in M14_BOKFORING_INVARIANTER}
    m |= {f"{n}_brudd": 0 for n in M14_BOKFORING_INVARIANTER}
    m["rundtur_paa_disponit_com"] = False
    art = {"krav_id": "m14-bokforing-v1", "ts": "2026-09-10T12:00:00Z",
           "bestatt": False, "oppsett": {"modul": "m14_fakturakontroll",
                                         "commit": "a" * 40, "vert": "v",
                                         "tenant": "t"},
           "maalt": m, "funn": []}
    assert valider_artefaktformat(art, "m14-bokforing-v1") == []
    assert valider_artefaktformat({**art, "maalt": {**m, "x": 1}},
                                  "m14-bokforing-v1")
