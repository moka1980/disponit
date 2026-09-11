"""Porten for M-6 svararmen, PR 5: bevisgrensen `m6-svar-v1`.

  1. Grensen er registrert med elleve punkter i parformen, og hvert
     punkt har en NAVNGITT port i utkast-/utsendingstestene.
  2. Artefaktskjemaet er generert fra invariantene.
  3. `m6-v1` (planens §7) og `m6-inntak-v1` står urørt: svararmen
     flipper ingen av deres sjekklistepunkter.

  EIERVEDTAKET 10/9 ER SELVE PREMISSET, og det måles: punkt 6 er den
  eneste invarianten her som ikke handler om en lekkasje, men om en
  DESIGNBESLUTNING — sendingen skal aldri bli en agenthandling igjen.
  En port som bare målte kryptering ville vært grønn den dagen noen la
  policyporten tilbake.
"""
import importlib

#: Punkt → (testmodul, testfunksjon). Porten under måler at hver finnes.
PUNKT_PORTER = {
    "utkast_i_klartekst_i_basen": [
        ("test_m6_svarutkast_port",
         "test_utkastet_skrives_kryptert_og_fodes_foreslatt")],
    "dom_uten_aktor_eller_tidspunkt": [
        ("test_m6_svarutkast_port",
         "test_dommen_baerer_aktor_og_felles_en_gang")],
    "sendt_satt_av_en_dom": [
        ("test_m6_svarutkast_port", "test_sendt_er_aldri_en_dom")],
    "utkast_til_slettet_melding": [
        ("test_m6_svarutkast_port",
         "test_utkast_til_en_slettet_melding_finnes_ikke")],
    "utkastvei_uten_eget_scope": [
        ("test_m6_svarutkast_port",
         "test_scopet_er_utkastets_ikke_kildens")],
    "sendingen_ble_en_agenthandling": [
        ("test_m6_svarutkast_port",
         "test_mennesket_sender_selv_uten_policyport")],
    "sendt_uten_samtykkets_sendescope": [
        ("test_m6_svarutkast_port",
         "test_en_postboks_uten_sendetilgang_sender_ikke")],
    "sendt_noe_som_ikke_sto_i_ko": [
        ("test_m6_utsending_port", "test_bare_et_utkast_i_ko_sendes")],
    "utsending_med_egen_mottaker": [
        ("test_m6_utsending_port",
         "test_utsendingen_bruker_reply_og_aldri_en_egen_mottaker"),
        ("test_m6_utsending_port", "test_svaret_gaar_ut_som_reply_i_traaden")],
    "feilgrunn_med_persondata": [
        ("test_m6_utsending_port", "test_feilgrunnen_er_en_kode")],
    "forbigaende_feil_konsumerte_koen": [
        ("test_m6_utsending_port", "test_5xx_er_drift_men_401_er_en_dom")],
}


def test_bevisgrensen_har_elleve_punkter_med_navngitte_porter():
    from manifestskjema import (KRAVGRENSER, M6_INNTAK_INVARIANTER,
                                M6_INVARIANTER, M6_SVAR_INVARIANTER,
                                _sjekk_grenser)
    g = KRAVGRENSER["m6-svar-v1"]
    assert len(M6_SVAR_INVARIANTER) == len(set(M6_SVAR_INVARIANTER)) == 11
    assert g["invarianter"] is M6_SVAR_INVARIANTER
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["krav_ja"] == ("rundtur_paa_disponit_com",)
    assert g["punktbinding"] == {}
    for inv in M6_SVAR_INVARIANTER:
        assert inv in PUNKT_PORTER, f"punktet {inv} har ingen navngitt port"
        for modul, navn in PUNKT_PORTER[inv]:
            m = importlib.import_module(f"tests.{modul}")
            assert callable(getattr(m, navn, None)), (inv, modul, navn)
    assert set(PUNKT_PORTER) == set(M6_SVAR_INVARIANTER)
    # De to eldre M-6-grensene er urørt: svararmen flipper ingenting.
    assert len(M6_INVARIANTER) == 7 and KRAVGRENSER["m6-v1"]["punktbinding"] == {}
    assert len(M6_INNTAK_INVARIANTER) == 10
    assert KRAVGRENSER["m6-inntak-v1"]["punktbinding"] == {}

    def art(**over):
        m = {f"{n}_forsok": 1 for n in M6_SVAR_INVARIANTER}
        m |= {f"{n}_brudd": 0 for n in M6_SVAR_INVARIANTER}
        m["rundtur_paa_disponit_com"] = True
        m.update(over)
        return {"krav_id": "m6-svar-v1", "bestatt": True, "maalt": m}
    assert _sjekk_grenser("m6-svar-v1", art()) == []
    # Hvert punkt kan felle grensen, i BEGGE retninger.
    for inv in M6_SVAR_INVARIANTER:
        assert any(f"{inv}_brudd=1" in f for f in
                   _sjekk_grenser("m6-svar-v1", art(**{f"{inv}_brudd": 1}))), inv
        assert any(f"{inv}_forsok=0" in f for f in
                   _sjekk_grenser("m6-svar-v1",
                                  art(**{f"{inv}_forsok": 0}))), inv
    for verdi in (False, None, 1, "ja"):
        assert _sjekk_grenser("m6-svar-v1",
                              art(rundtur_paa_disponit_com=verdi)), verdi


def test_artefaktskjemaet_er_generert_fra_invariantene():
    import json
    from pathlib import Path

    from manifestskjema import (ARTEFAKTSKJEMAER, M6_SVAR_INVARIANTER,
                                valider_artefaktformat)
    rot = Path(__file__).resolve().parents[1]
    sk = json.loads((rot / ARTEFAKTSKJEMAER["m6-svar-v1"])
                    .read_text(encoding="utf-8"))
    felt = set(sk["properties"]["maalt"]["required"])
    assert felt == ({f"{n}_forsok" for n in M6_SVAR_INVARIANTER}
                    | {f"{n}_brudd" for n in M6_SVAR_INVARIANTER}
                    | {"rundtur_paa_disponit_com"})
    assert set(sk["properties"]["maalt"]["properties"]) == felt
    assert sk["properties"]["krav_id"] == {"const": "m6-svar-v1"}
    assert sk["properties"]["oppsett"]["properties"]["modul"] == {
        "const": "m06_epost"}
    m = {f"{n}_forsok": 1 for n in M6_SVAR_INVARIANTER}
    m |= {f"{n}_brudd": 0 for n in M6_SVAR_INVARIANTER}
    m["rundtur_paa_disponit_com"] = False
    art = {"krav_id": "m6-svar-v1", "ts": "2026-09-10T12:00:00Z",
           "bestatt": False, "oppsett": {"modul": "m06_epost",
                                         "commit": "a" * 40, "vert": "v",
                                         "tenant": "t"},
           "maalt": m, "funn": []}
    assert valider_artefaktformat(art, "m6-svar-v1") == []
    # Et felt som ikke er en invariant hører ikke hjemme i målingen.
    assert valider_artefaktformat({**art, "maalt": {**m, "x": 1}},
                                  "m6-svar-v1")


def test_sendingen_er_ikke_en_oppdragstype():
    """PUNKT 6, målt der det bor: eiervedtaket sier at menneskets eget
    svar ikke er en agenthandling. Blir det en oppdragstype igjen, er
    grensen løgn — og porten faller HER, ikke først i en artefaktrunde
    noen kanskje kjører."""
    from oppdragskontrakt import OPPDRAGSTYPER
    assert not [n for n in OPPDRAGSTYPER if n.startswith("epost.svar")]
