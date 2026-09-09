"""Porten for ARC B, PR 6: flaten og bevisgrensen.

  1. `GET /v1/fordring` bærer avsenderprofilen (null før den er satt,
     navn + svar-til etter), og `GET …/hendelser` bærer purringene per
     trinn med utfall — aldri adressen.
  2. Bevisgrensen `m23-purring-v1` er registrert med ti punkter i
     parformen, og hvert punkt har en NAVNGITT port i ARC B-testene
     (tabellen under er bindingen; m23-v1-regelen «invarianten må stå
     i en testfil»).

Punkt → port:
  adresse_i_klartekst_utenfor_fordringen → test_m23_sending_port
      (kvittering/payload uten adresse), test_m23_controller
      (adressen aldri i kvitteringen), denne (hendelser uten adresse)
  bestilling_uten_policy → test_m23_purringsutloser_port
      (policy uten purring bestiller ingenting)
  sending_uten_mottaker → test_m23_purringsutloser_port (kandidatdøra),
      test_m23_sending_port (uten adresse → feilet uten sending)
  dobbel_bestilling_samme_trinn → test_m23_purringsutloser_port
      (runde to gjør ingenting), test_bestilling_purring_port (frekvens)
  dobbel_sending_samme_oppdrag → test_m23_controller (uvisst = terminalt),
      test_m23_bokforing_port (gjenspill idempotent)
  trinn_valgt_av_bestiller → test_bestilling_purring_port (lukket
      kontrakt: `trinn` i kroppen avvises), test_m23_controller
      (trinn_flyttet)
  policygrense_omgaatt → test_bestilling_purring_port (frekvens, under
      minimum), test_m23_purringsutloser_port (under minimum → brudd)
  inkasso_sendt_automatisk → test_bestilling_purring_port
      (FELTVERDIER uten inkasso), test_m23_controller (maler uten inkasso)
  kvittering_uten_bokforing → test_m23_bokforing_port
  kill_switch_konsumerte_trinn → test_m23_purringsutloser_port
      (kill-switch, plattformtilstand)
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m23_purringsutloser_port import _bransjemal, _pa, _runde
from .test_m23_sending_port import _fordring_med_adresse, _post

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


@pg_plan
def test_flaten_ser_avsenderen_og_purringene_men_aldri_adressen(
        migrator, miljo, app, klient, token):
    _bransjemal(migrator)
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    hode = {"authorization": f"Bearer {tok}"}
    # Ingen profil ennå → null (flaten sier det høyt).
    from .test_m37 import _sett_kontekst
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE purreplan SET avsender_navn=NULL, svar_til=NULL"
                     " WHERE tenant=%s", (TENANT,))
    migrator.commit()
    r = klient.get("/v1/fordring", headers=hode)
    assert r.status_code == 200, r.text
    assert r.json()["avsender"] is None, r.json()["avsender"]
    r = _post(klient, tok, "/v1/fordring/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "regnskap@fjordlys.example"})
    assert r.status_code == 200, r.text
    r = klient.get("/v1/fordring", headers=hode)
    a = r.json()["avsender"]
    assert a["avsender_navn"] == "Fjordlys Elektro AS"
    assert a["svar_til"] == "regnskap@fjordlys.example" and a["oppdatert"]
    adresse = "k-" + secrets.token_hex(3) + "@nordvik.example"
    fid = _fordring_med_adresse(klient, tok, adresse)
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["fordring"] == str(fid)]
    assert mine and mine[0]["utfall"] == "tillat", res
    r = klient.get(f"/v1/fordring/{fid}/hendelser", headers=hode)
    assert r.status_code == 200, r.text
    p = r.json()["purringer"]
    assert len(p) == 1 and p[0]["trinn"] == 1 \
        and p[0]["handling_trinn"] == "paaminnelse" \
        and p[0]["utfall"] == "tillat" and p[0]["oppdrag_id"] \
        and p[0]["bestilt_ts"], p
    assert adresse not in r.text
    r = klient.get("/v1/fordring", headers=hode)
    assert adresse not in r.text
    rad = [f for f in r.json()["fordringer"] if f["fordring_id"] == str(fid)]
    assert rad and rad[0]["mottaker_maske"].startswith("k****@")


def test_bevisgrensen_har_ti_punkter_med_navngitte_porter():
    from pathlib import Path
    from manifestskjema import (KRAVGRENSER, M23_PURRING_INVARIANTER,
                                _sjekk_grenser)
    g = KRAVGRENSER["m23-purring-v1"]
    assert len(M23_PURRING_INVARIANTER) == \
        len(set(M23_PURRING_INVARIANTER)) == 10
    assert g["invarianter"] is M23_PURRING_INVARIANTER
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["krav_ja"] == ("rundtur_paa_disponit_com",)
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in M23_PURRING_INVARIANTER:
        assert inv in egen, f"punktet {inv} har ingen navngitt port"
    # Parformen måler: null forsøk er rødt, ett brudd er rødt, ja-punktet
    # må være bokstavelig true.
    def art(**over):
        m = {f"{n}_forsok": 1 for n in M23_PURRING_INVARIANTER}
        m |= {f"{n}_brudd": 0 for n in M23_PURRING_INVARIANTER}
        m["rundtur_paa_disponit_com"] = True
        m.update(over)
        return {"krav_id": "m23-purring-v1", "bestatt": True, "maalt": m}
    assert _sjekk_grenser("m23-purring-v1", art()) == []
    assert _sjekk_grenser("m23-purring-v1",
                          art(kvittering_uten_bokforing_brudd=1))
    assert _sjekk_grenser("m23-purring-v1",
                          art(dobbel_sending_samme_oppdrag_forsok=0))
    assert _sjekk_grenser("m23-purring-v1",
                          art(rundtur_paa_disponit_com="ja"))
