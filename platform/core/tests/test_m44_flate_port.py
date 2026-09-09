"""Porten for ARC B kampanje, PR 6: flaten og bevisgrensen.

  1. `GET /v1/kampanje` bærer avsenderprofilen (null før den er satt,
     navn + svar-til etter) og leveransen per kampanje som TALL; og
     `GET …/kampanje/{id}/leveringer` bærer linjene per mottaker med
     referanse og maske — aldri adressen, aldri teksten.
  2. Bevisgrensen `m44-kampanje-v1` er registrert med ti punkter i
     parformen, og hvert punkt har en NAVNGITT port i ARC B-testene
     (tabellen under er bindingen; m44-v1-regelen «invarianten må stå
     i en testfil»).

Punkt → port:
  adresse_i_klartekst_utenfor_registeret → test_m44_sending_port
      (kvittering/payload uten adresse), test_m44_controller (adressen
      og teksten aldri i kvitteringen), test_m44_bokforing_port (saken
      uten adresse), test_m44_kampanjeutloser_port (evidens uten
      adresse), denne (flaten uten adresse)
  bestilling_uten_policy → test_m44_kampanjeutloser_port
      (policy uten kampanje bestiller ingenting)
  levering_uten_samtykke → test_m44_kampanjeutloser_port (aldri
      samtykket = ikke kandidat; trukket → brudd én gang),
      test_bestilling_kampanje_port (attestasjon_negativ),
      test_m44_sending_port (trukket mellom bestilling og claim)
  dobbel_bestilling_samme_par → test_m44_kampanjeutloser_port
      (runde to gjør ingenting), test_bestilling_kampanje_port (frekvens)
  dobbel_levering_samme_oppdrag → test_m44_controller (uvisst =
      terminalt), test_m44_bokforing_port (gjenspill idempotent)
  levering_uten_avmeldingslenke → test_m44_controller (malene:
      uten lenke leveres ingenting; lenken i hver e-post),
      test_bestilling_kampanje_port (attestasjonen `avmeldingslenke`)
  innhold_eller_adresse_mangler → test_bestilling_kampanje_port
      (409 kampanje_ikke_klar_for_levering),
      test_m44_kampanjeutloser_port (kandidatdøra),
      test_m44_controller (hindringer → feilet uten sending)
  policygrense_omgaatt → test_bestilling_kampanje_port (frekvens
      stopper den tredje)
  kvittering_uten_bokforing → test_m44_bokforing_port
  kill_switch_konsumerte_par → test_m44_kampanjeutloser_port
      (kill-switch, forbigående og plattformtilstand)
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_kampanje_port import _kampanjepolicy
from .test_m37 import _sett_kontekst
from .test_m44_kontakt_port import _post
from .test_m44_sending_port import (_bestilt_av_utloseren,
                                    _klar_med_ekte_adresse)

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


@pg_plan
def test_flaten_ser_avsenderen_og_leveransen_men_aldri_adressen(
        migrator, miljo, app, klient, token):
    _kampanjepolicy(migrator)
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    hode = {"authorization": f"Bearer {tok}"}
    _sett_kontekst(migrator, TENANT)
    migrator.execute("DELETE FROM kampanjeavsender WHERE tenant=%s",
                     (TENANT,))
    migrator.commit()
    r = klient.get("/v1/kampanje", headers=hode)
    assert r.status_code == 200, r.text
    assert r.json()["avsender"] is None, r.json()["avsender"]
    r = _post(klient, tok, "/v1/kampanje/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "post@fjordlys.example"})
    assert r.status_code == 200, r.text
    r = klient.get("/v1/kampanje", headers=hode)
    a = r.json()["avsender"]
    assert a["avsender_navn"] == "Fjordlys Elektro AS"
    assert a["svar_til"] == "post@fjordlys.example" and a["oppdatert"]
    adresse = "kari-" + secrets.token_hex(3) + "@example.com"
    kid, mid = _klar_med_ekte_adresse(klient, tok, adresse)
    oid = _bestilt_av_utloseren(app, kid)
    r = klient.get("/v1/kampanje", headers=hode)
    assert r.status_code == 200, r.text
    # Emnet står i listen (153-designet); TEKSTEN og adressen gjør det aldri.
    assert adresse not in r.text and "vi tilbyr" not in r.text.lower()
    rad = [k for k in r.json()["kampanjer"] if k["kampanje_id"] == str(kid)]
    assert rad and rad[0]["leveranse"] == {
        "bestilt": 1, "tillat": 1, "brudd": 0, "feil": 0, "levert": 0}, rad
    r = klient.get(f"/v1/kampanje/kampanje/{kid}/leveringer", headers=hode)
    assert r.status_code == 200, r.text
    linjer = r.json()["linjer"]
    assert len(linjer) == 1 and linjer[0]["mottaker_id"] == str(mid) \
        and linjer[0]["utfall"] == "tillat" \
        and linjer[0]["oppdrag_id"] == oid \
        and linjer[0]["levert_ts"] is None \
        and linjer[0]["kontakt_maske"].startswith("k****@"), linjer
    assert adresse not in r.text
    # Ukjent kampanje: tom liste, ikke 500.
    import uuid
    r = klient.get(f"/v1/kampanje/kampanje/{uuid.uuid4()}/leveringer",
                   headers=hode)
    assert r.status_code == 200 and r.json()["linjer"] == [], r.text


def test_bevisgrensen_har_ti_punkter_med_navngitte_porter():
    from pathlib import Path
    from manifestskjema import (KRAVGRENSER, M44_KAMPANJE_INVARIANTER,
                                _sjekk_grenser)
    g = KRAVGRENSER["m44-kampanje-v1"]
    assert len(M44_KAMPANJE_INVARIANTER) == \
        len(set(M44_KAMPANJE_INVARIANTER)) == 10
    assert g["invarianter"] is M44_KAMPANJE_INVARIANTER
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["krav_ja"] == ("rundtur_paa_disponit_com",)
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in M44_KAMPANJE_INVARIANTER:
        assert inv in egen, f"punktet {inv} har ingen navngitt port"

    def art(**over):
        m = {f"{n}_forsok": 1 for n in M44_KAMPANJE_INVARIANTER}
        m |= {f"{n}_brudd": 0 for n in M44_KAMPANJE_INVARIANTER}
        m["rundtur_paa_disponit_com"] = True
        m.update(over)
        return {"krav_id": "m44-kampanje-v1", "bestatt": True, "maalt": m}
    assert _sjekk_grenser("m44-kampanje-v1", art()) == []
    assert _sjekk_grenser("m44-kampanje-v1",
                          art(kvittering_uten_bokforing_brudd=1))
    assert _sjekk_grenser("m44-kampanje-v1",
                          art(dobbel_levering_samme_oppdrag_forsok=0))
    assert _sjekk_grenser("m44-kampanje-v1",
                          art(rundtur_paa_disponit_com="ja"))


def test_artefaktskjemaet_er_generert_fra_invariantene():
    """Skjemaet er ikke skrevet for hånd: feltsettet ER invariantene i
    parform pluss ja-punktet, og `valider_artefaktformat` bruker det."""
    import json
    from pathlib import Path
    from manifestskjema import (ARTEFAKTSKJEMAER, M44_KAMPANJE_INVARIANTER,
                                valider_artefaktformat)
    rot = Path(__file__).resolve().parents[1]
    sk = json.loads((rot / ARTEFAKTSKJEMAER["m44-kampanje-v1"])
                    .read_text(encoding="utf-8"))
    felt = set(sk["properties"]["maalt"]["required"])
    assert felt == ({f"{n}_forsok" for n in M44_KAMPANJE_INVARIANTER}
                    | {f"{n}_brudd" for n in M44_KAMPANJE_INVARIANTER}
                    | {"rundtur_paa_disponit_com"})
    assert sk["properties"]["oppsett"]["properties"]["modul"] == {
        "const": "m44_kampanje"}
    m = {f"{n}_forsok": 1 for n in M44_KAMPANJE_INVARIANTER}
    m |= {f"{n}_brudd": 0 for n in M44_KAMPANJE_INVARIANTER}
    m["rundtur_paa_disponit_com"] = False
    art = {"krav_id": "m44-kampanje-v1", "ts": "2026-09-09T12:00:00Z",
           "bestatt": False, "oppsett": {"modul": "m44_kampanje",
                                         "commit": "a" * 40, "vert": "v",
                                         "tenant": "t"},
           "maalt": m, "funn": []}
    assert valider_artefaktformat(art, "m44-kampanje-v1") == []
    assert valider_artefaktformat({**art, "maalt": {**m, "x": 1}},
                                  "m44-kampanje-v1")
