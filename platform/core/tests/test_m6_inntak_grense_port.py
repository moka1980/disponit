"""Porten for M-6 inntak, PR 3: bevisgrensen `m6-inntak-v1`.

  1. Grensen er registrert med ti punkter i parformen, og hvert punkt
     har en NAVNGITT port i inntaks-/flate-/088-testene.
  2. Artefaktskjemaet er generert fra invariantene.
  3. `m6-v1` (planens §7) står urørt: punktbindingen er fortsatt tom,
     og settet er fortsatt sju — inntaket flipper ingen
     sjekklistepunkter.
"""
import importlib

#: Punkt → (testmodul, testfunksjon). Porten under måler at hver finnes.
PUNKT_PORTER = {
    "innhenting_duplikatmelding": [
        ("test_m6_inntak_port", "test_runden_henter_krypterer_og_er_idempotent"),
        ("test_m6_epost", "test_port1_duplikat_leverandormelding_er_en_rad")],
    "persondata_i_klartekst_i_basen": [
        ("test_m6_inntak_port", "test_runden_henter_krypterer_og_er_idempotent"),
        ("test_m6_meldinger_port",
         "test_flaten_dekrypterer_for_okten_og_bare_for_tenanten")],
    "kilde_credentials_ukryptert": [
        ("test_m6_epost", "test_port2_credentials_er_ciphertext_aldri_klartekst")],
    "logg_med_persondata": [
        ("test_m6_inntak_port", "test_runden_henter_krypterer_og_er_idempotent")],
    "kill_switch_konsumerte_kilder": [
        ("test_m6_inntak_port",
         "test_kill_switch_deaktivert_kilde_og_tenantkontekst")],
    "deaktivert_kilde_hentet": [
        ("test_m6_inntak_port",
         "test_kill_switch_deaktivert_kilde_og_tenantkontekst"),
        ("test_m6_inntak_port",
         "test_autfeil_setter_feilet_men_forbigaende_ror_ingenting")],
    "autfeil_uten_feilet_kilde": [
        ("test_m6_inntak_port",
         "test_autfeil_setter_feilet_men_forbigaende_ror_ingenting")],
    "forbigaende_feil_konsumerte_kilden": [
        ("test_m6_inntak_port",
         "test_autfeil_setter_feilet_men_forbigaende_ror_ingenting")],
    "slettet_melding_med_tekst": [
        ("test_m6_meldinger_port",
         "test_flaten_dekrypterer_for_okten_og_bare_for_tenanten"),
        ("test_m6_slett_port", "test_mennesket_sletter_naa_og_sporet_bestar"),
        ("test_m6_epost", "test_port4_reaping_tommer_alle_lagrene")],
    "modul_sendevei_finnes": [
        ("test_m6_inntak_port", "test_innhenteren_er_kun_lesende"),
        ("test_m6_slett_port", "test_slettingen_krever_forvaltningsscopet"),
        ("test_m6_meldinger_port", "test_rutene_er_lesende_med_epost_read"),
        ("test_m6_epost", "test_port5_modulen_har_ingen_sendevei")],
}


def test_bevisgrensen_har_ti_punkter_med_navngitte_porter():
    from manifestskjema import (KRAVGRENSER, M6_INNTAK_INVARIANTER,
                                M6_INVARIANTER, _sjekk_grenser)
    g = KRAVGRENSER["m6-inntak-v1"]
    assert len(M6_INNTAK_INVARIANTER) == len(set(M6_INNTAK_INVARIANTER)) == 10
    assert g["invarianter"] is M6_INNTAK_INVARIANTER
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["krav_ja"] == ("rundtur_paa_disponit_com",)
    for inv in M6_INNTAK_INVARIANTER:
        assert inv in PUNKT_PORTER, f"punktet {inv} har ingen navngitt port"
        for modul, navn in PUNKT_PORTER[inv]:
            m = importlib.import_module(f"tests.{modul}")
            assert callable(getattr(m, navn, None)), (inv, modul, navn)
    assert set(PUNKT_PORTER) == set(M6_INNTAK_INVARIANTER)
    # m6-v1 urørt: inntaket flipper ingen sjekklistepunkter.
    assert len(M6_INVARIANTER) == 7 and KRAVGRENSER["m6-v1"]["punktbinding"] == {}

    def art(**over):
        m = {f"{n}_forsok": 1 for n in M6_INNTAK_INVARIANTER}
        m |= {f"{n}_brudd": 0 for n in M6_INNTAK_INVARIANTER}
        m["rundtur_paa_disponit_com"] = True
        m.update(over)
        return {"krav_id": "m6-inntak-v1", "bestatt": True, "maalt": m}
    assert _sjekk_grenser("m6-inntak-v1", art()) == []
    assert _sjekk_grenser("m6-inntak-v1", art(logg_med_persondata_brudd=1))
    assert _sjekk_grenser("m6-inntak-v1",
                          art(innhenting_duplikatmelding_forsok=0))
    assert _sjekk_grenser("m6-inntak-v1", art(rundtur_paa_disponit_com="ja"))


def test_artefaktskjemaet_er_generert_fra_invariantene():
    import json
    from pathlib import Path
    from manifestskjema import (ARTEFAKTSKJEMAER, M6_INNTAK_INVARIANTER,
                                valider_artefaktformat)
    rot = Path(__file__).resolve().parents[1]
    sk = json.loads((rot / ARTEFAKTSKJEMAER["m6-inntak-v1"])
                    .read_text(encoding="utf-8"))
    felt = set(sk["properties"]["maalt"]["required"])
    assert felt == ({f"{n}_forsok" for n in M6_INNTAK_INVARIANTER}
                    | {f"{n}_brudd" for n in M6_INNTAK_INVARIANTER}
                    | {"rundtur_paa_disponit_com"})
    assert set(sk["properties"]["maalt"]["properties"]) == felt
    assert sk["properties"]["oppsett"]["properties"]["modul"] == {
        "const": "m06_epost"}
    m = {f"{n}_forsok": 1 for n in M6_INNTAK_INVARIANTER}
    m |= {f"{n}_brudd": 0 for n in M6_INNTAK_INVARIANTER}
    m["rundtur_paa_disponit_com"] = False
    art = {"krav_id": "m6-inntak-v1", "ts": "2026-09-10T12:00:00Z",
           "bestatt": False, "oppsett": {"modul": "m06_epost",
                                         "commit": "a" * 40, "vert": "v",
                                         "tenant": "t"},
           "maalt": m, "funn": []}
    assert valider_artefaktformat(art, "m6-inntak-v1") == []
    assert valider_artefaktformat({**art, "maalt": {**m, "x": 1}},
                                  "m6-inntak-v1")


def test_bevisartefaktet_passerer_grensen():
    """Bevisrunden 10/9 mot disponit.com: artefaktet er innsjekket, har
    skjemaets form og passerer `m6-inntak-v1` — og ja-punktet er
    bokstavelig true. Et artefakt som endres for hånd skal måles på nytt."""
    import json
    from pathlib import Path
    from manifestskjema import (M6_INNTAK_INVARIANTER, _sjekk_grenser,
                                valider_artefaktformat)
    rot = Path(__file__).resolve().parents[3]
    fil = rot / ("deploy/staging/artefakter/"
                 "m6-inntak-v1-20260910T164000Z.json")
    art = json.loads(fil.read_text(encoding="utf-8"))
    assert valider_artefaktformat(art, "m6-inntak-v1") == []
    assert _sjekk_grenser("m6-inntak-v1", art) == []
    assert art["maalt"]["rundtur_paa_disponit_com"] is True
    for inv in M6_INNTAK_INVARIANTER:
        assert art["maalt"][f"{inv}_forsok"] >= 1
    # De gule funnene er NAVNGITT — et artefakt uten dem påstår mer enn
    # runden målte. `flatefunn_etter_runden` er den ærligste: grensen
    # måler inntaket, ikke om det som ble hentet er til å lese.
    nokler = {f["tekstnokkel"] for f in art["funn"]}
    for n in ("m6.inntak.punkt_maalt_kun_i_port",
              "m6.inntak.flatefunn_etter_runden",
              "m6.inntak.bare_innboksen",
              "m6.inntak.ingen_klassifisering",
              "m6.inntak.runden_logger_bare_ved_kandidater"):
        assert n in nokler, n
    assert art["oppsett"]["modul"] == "m06_epost"

