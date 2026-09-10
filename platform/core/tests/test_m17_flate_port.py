"""Porten for ARC B kundeservice, PR 6: flaten og bevisgrensen.

  1. `GET /v1/kundeservice` bærer avsenderprofilen (null før den er satt,
     navn + svar-til + signatur etter), og `GET …/utkast` bærer per
     utkast hva utløseren gjorde (`bestilling`: utfall, oppdrag, sak) —
     aldri adressen utenfor innsynsscopet.
  2. Bevisgrensen `m17-svar-v1` er registrert med ti punkter i parformen,
     og hvert punkt har en NAVNGITT port i ARC B-testene.

Punkt → port:
  adresse_eller_tekst_i_klartekst_utenfor_registeret →
      test_m17_avsender_port (køen bærer masken, aldri adressen),
      test_m17_sending_port (kvittering/payload uten adresse og tekst),
      test_m17_controller (kvitteringen uten adresse/tekst),
      test_bestilling_svar_port (saken bærer koder, aldri tekst),
      test_m17_bokforing_port (saken uten adresse og tekst)
  bestilling_uten_policy → test_m17_svarutloser_port
  sending_uten_godkjenning → test_m17_svarutloser_port (kandidatdøra),
      test_bestilling_svar_port (ikke godkjent → sak),
      test_m17_sending_port (tilstanden spurt en gang til)
  dobbel_bestilling_samme_utkast → test_m17_svarutloser_port (runde to)
  dobbel_sending_samme_oppdrag → test_m17_controller (uvisst = terminalt),
      test_m17_bokforing_port (gjenspill idempotent)
  personopplysning_i_svaret → test_svarkontroll, test_bestilling_svar_port
  okonomisk_lofte_i_svaret → test_svarkontroll, test_bestilling_svar_port
  svar_uten_mottaker_eller_svarvei → test_bestilling_svar_port
      (målportene), test_m17_svarutloser_port (kandidatdøra),
      test_m17_sending_port (lukket mellom bestilling og claim)
  kvittering_uten_bokforing → test_m17_bokforing_port
  kill_switch_konsumerte_utkast → test_m17_svarutloser_port
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_svar_port import _klar, _svarpolicy
from .test_m17_avsender_port import _post
from .test_m17_sending_port import _bestilt_av_utloseren
from .test_m37 import _sett_kontekst

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


@pg_plan
def test_flaten_ser_avsenderen_og_bestillingen_men_aldri_adressen(
        migrator, miljo, app, klient, token):
    _svarpolicy(migrator)
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "decisions:read",
                           "kundeservice:innhold"))
    hode = {"authorization": f"Bearer {tok}"}
    _sett_kontekst(migrator, TENANT)
    migrator.execute("DELETE FROM kundeserviceavsender WHERE tenant=%s",
                     (TENANT,))
    migrator.commit()
    r = klient.get("/v1/kundeservice", headers=hode)
    assert r.status_code == 200 and r.json()["avsenderprofil"] is None, r.text
    r = _post(klient, tok, "/v1/kundeservice/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "post@fjordlys.example", "signatur": "Fjordlys"})
    assert r.status_code == 200, r.text
    r = klient.get("/v1/kundeservice", headers=hode)
    a = r.json()["avsenderprofil"]
    assert a["avsender_navn"] == "Fjordlys Elektro AS" \
        and a["svar_til"] == "post@fjordlys.example" \
        and a["signatur"] == "Fjordlys" and a["oppdatert"]
    adresse = "kari-" + secrets.token_hex(3) + "@nordvik.example"
    hid, uid = _klar(klient, tok, adresse=adresse)
    oid = _bestilt_av_utloseren(app, hid)
    r = klient.get("/v1/kundeservice", headers=hode)
    assert adresse not in r.text
    h = [x for x in r.json()["koe"] if x["henvendelse_id"] == hid][0]
    assert h["avsender_maske"].startswith("k****@") and h["godkjent_utkast"]
    r = klient.get(f"/v1/kundeservice/henvendelse/{hid}/utkast", headers=hode)
    assert r.status_code == 200, r.text
    u = [x for x in r.json()["utkast"] if x["utkast_id"] == uid][0]
    assert u["status"] == "godkjent"
    assert u["bestilling"]["utfall"] == "tillat" \
        and u["bestilling"]["oppdrag_id"] == oid \
        and u["bestilling"]["unntak_id"] is None \
        and u["bestilling"]["bestilt_ts"], u
    assert adresse not in r.text


def test_bevisgrensen_har_ti_punkter_med_navngitte_porter():
    from pathlib import Path
    from manifestskjema import (KRAVGRENSER, M17_SVAR_INVARIANTER,
                                _sjekk_grenser)
    g = KRAVGRENSER["m17-svar-v1"]
    assert len(M17_SVAR_INVARIANTER) == len(set(M17_SVAR_INVARIANTER)) == 10
    assert g["invarianter"] is M17_SVAR_INVARIANTER
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["krav_ja"] == ("rundtur_paa_disponit_com",)
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in M17_SVAR_INVARIANTER:
        assert inv in egen, f"punktet {inv} har ingen navngitt port"

    def art(**over):
        m = {f"{n}_forsok": 1 for n in M17_SVAR_INVARIANTER}
        m |= {f"{n}_brudd": 0 for n in M17_SVAR_INVARIANTER}
        m["rundtur_paa_disponit_com"] = True
        m.update(over)
        return {"krav_id": "m17-svar-v1", "bestatt": True, "maalt": m}
    assert _sjekk_grenser("m17-svar-v1", art()) == []
    assert _sjekk_grenser("m17-svar-v1", art(kvittering_uten_bokforing_brudd=1))
    assert _sjekk_grenser("m17-svar-v1",
                          art(dobbel_sending_samme_oppdrag_forsok=0))
    assert _sjekk_grenser("m17-svar-v1", art(rundtur_paa_disponit_com="ja"))


def test_artefaktskjemaet_er_generert_fra_invariantene():
    import json
    from pathlib import Path
    from manifestskjema import (ARTEFAKTSKJEMAER, M17_SVAR_INVARIANTER,
                                valider_artefaktformat)
    rot = Path(__file__).resolve().parents[1]
    sk = json.loads((rot / ARTEFAKTSKJEMAER["m17-svar-v1"])
                    .read_text(encoding="utf-8"))
    felt = set(sk["properties"]["maalt"]["required"])
    assert felt == ({f"{n}_forsok" for n in M17_SVAR_INVARIANTER}
                    | {f"{n}_brudd" for n in M17_SVAR_INVARIANTER}
                    | {"rundtur_paa_disponit_com"})
    assert sk["properties"]["oppsett"]["properties"]["modul"] == {
        "const": "m17_kundeservice"}
    m = {f"{n}_forsok": 1 for n in M17_SVAR_INVARIANTER}
    m |= {f"{n}_brudd": 0 for n in M17_SVAR_INVARIANTER}
    m["rundtur_paa_disponit_com"] = False
    art = {"krav_id": "m17-svar-v1", "ts": "2026-09-09T20:00:00Z",
           "bestatt": False, "oppsett": {"modul": "m17_kundeservice",
                                         "commit": "a" * 40, "vert": "v",
                                         "tenant": "t"},
           "maalt": m, "funn": []}
    assert valider_artefaktformat(art, "m17-svar-v1") == []
    assert valider_artefaktformat({**art, "maalt": {**m, "x": 1}},
                                  "m17-svar-v1")


def test_bevisartefaktet_passerer_grensen():
    """Bevisrunden 9/9–10/9 mot disponit.com: artefaktet er innsjekket,
    har skjemaets form og passerer `m17-svar-v1` — og ja-punktet er
    bokstavelig true. Et artefakt som endres for hånd skal måles på nytt."""
    import json
    from pathlib import Path
    from manifestskjema import (M17_SVAR_INVARIANTER, _sjekk_grenser,
                                valider_artefaktformat)
    rot = Path(__file__).resolve().parents[3]
    fil = rot / "deploy/staging/artefakter/m17-svar-v1-20260910T051000Z.json"
    art = json.loads(fil.read_text(encoding="utf-8"))
    assert valider_artefaktformat(art, "m17-svar-v1") == []
    assert _sjekk_grenser("m17-svar-v1", art) == []
    assert art["maalt"]["rundtur_paa_disponit_com"] is True
    for inv in M17_SVAR_INVARIANTER:
        assert art["maalt"][f"{inv}_forsok"] >= 1
    # De gule funnene er NAVNGITT — et artefakt uten dem påstår mer enn
    # runden målte.
    nokler = {f["tekstnokkel"] for f in art["funn"]}
    assert "m17.svar.punkt_maalt_kun_i_port" in nokler
    assert "m17.svar.ugodkjent_bestilling_felles_paa_rollen" in nokler
    assert "m17.svar.m37_verifikator_mangler" in nokler
    assert "m17.svar.runden_logger_bare_ved_kandidater" in nokler
    # Plassholderen skal være byttet ut med eiers ord før innsjekk.
    assert "PLASSHOLDER" not in art["oppsett"]["notat"]
