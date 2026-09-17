"""«Likt lokalt»-leddet for M-14s fasitartefakt — STÅENDE måling.

Det samme settet (`deploy/staging/m14_fasit.py`) som staging-artefaktet
drives av, kjøres her gjennom de EKTE dørene lokalt: kundens dører som
runtime (terskler, satser, leverandør i M-24, faktura med kontrollene
døra kjører, manuell kontroll, avgjørelse, terskel strammet),
fakturasveipen som SVEIPEROLLEN, og flaten lest gjennom
`api.faktura.svar_for`.

MUTASJONENE SOM FELLER DENNE (hver kjørt som eierrollen, hver på sin
navngitte rad):
  1. `p_dag - f.mottatt > v_t.kontrollfrist_dogn` → `>=`:
     «paa_fristen_7» blir et funn.
  2. `f.brutto_ore > v_t.belopsgrense_ore` → `>=`: «paa_grensen» blir
     et funn.
  3. `abs(f2.utstedt - f.utstedt) <= v_t.dublettvindu_dogn` → `<`:
     «dublett_a»/«dublett_b» er ikke lenger funn.
  4. `faktura.registrert`-evidensen omdøpt: flaten riktig, kjeden mangler.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

ROT = Path(__file__).resolve().parents[3]
SVEIP_DSN = os.environ.get("DISPONIT_TEST_FAKTURASVEIP_DSN")

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN),
                        reason="test-DSN ikke satt")
roller = pytest.mark.skipif(not SVEIP_DSN,
                            reason="FAKTURASVEIP-DSN ikke satt")


def _last(navn: str, fil: str):
    spec = importlib.util.spec_from_file_location(navn, ROT / fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    return _last("m14_fasit", "deploy/staging/m14_fasit.py")


def _rt():
    from db.pg import koble
    return koble(DSN)


def test_settet_er_fasiten():
    """Settet ER kantene — og grensene er lest av det."""
    m = _lib()
    sett = dict(m.bygg_sett())
    med = sett["med_terskel"]
    rad = {r[0]: r for r in med}
    E, F = m.TERSKLER_ETTER, m.TERSKLER_FOR
    # Mva-kanten: avvik 1 øre (= slingringen, ikke funn) og 2 (funn).
    assert rad["mva_paa_slingringen_1"][3] - 2_500 == E["slingring"]
    assert rad["mva_avvik_2"][3] - 2_500 == E["slingring"] + 1
    assert rad["mva_avvik_2"][8] == {"mva_avvik"} and not rad["mva_paa_slingringen_1"][8]
    # Dublettkanten: tre døgn (= vinduet, funn for begge) og fire (ikke).
    assert rad["dublett_b"][5] - rad["dublett_a"][5] == E["vindu"]
    assert rad["dublett_utenfor_b"][5] - rad["dublett_utenfor_a"][5] == E["vindu"] + 1
    assert rad["dublett_a"][8] == rad["dublett_b"][8] == {"naer_dublett"}
    # Beløpskanten: nøyaktig på grensen (ikke funn) og over (funn) — og
    # over grensen ble registrert UNDER den romslige grensen.
    brutto = lambda r: r[2] + r[3]  # noqa: E731
    assert brutto(rad["paa_grensen"]) == E["grense"]
    assert F["grense"] > brutto(rad["over_grensen_etter_stramming"]) > E["grense"]
    assert rad["over_grensen_etter_stramming"][8] == {"over_belopsgrense"}
    assert rad["over_grensen_manuelt"][7] == "manuell" and not rad["over_grensen_manuelt"][8]
    # Fristkanten: mottatt for 7 døgn siden (= fristen, ikke funn) og 8.
    assert rad["paa_fristen_7"][6] == E["frist"] and rad["ukontrollert_8"][6] == E["frist"] + 1
    assert F["frist"] > rad["ukontrollert_8"][6]
    # Ingen sats, ukjent leverandør, kontrollert, avvist.
    assert rad["ingen_sats"][4] == "lav" and rad["ingen_sats"][8] == {"ingen_mvasats"}
    assert rad["ukjent_leverandor"][1] == m.UKJENT
    assert rad["kontrollert_med_avvik"][7] == "kontroller" and not rad["kontrollert_med_avvik"][8]
    assert rad["avvist"][7] == "avvis" and not rad["avvist"][8]
    uten = sett["uten_terskel"]
    assert sum(1 for r in uten if "ingen_terskel" in r[8]) == 2
    assert any(r[7] == "kontroller" and not r[8] for r in uten)
    from manifestskjema import KRAVGRENSER
    kg = KRAVGRENSER["m14-fasit-v1"]
    assert sum(len(r) for r in sett.values()) == kg["fakturaer_eksakt"]
    assert sum(len(r[8]) for rader in sett.values() for r in rader) \
        == kg["ventede_funn_eksakt"]
    assert sum(sum(m.forventet_evidens(r, k == "med_terskel").values())
               for k, r in sett.items()) == kg["evidenshendelser_eksakt"]


def test_settet_er_bundet_til_bytene_ikke_til_navnet_sitt():
    import hashlib
    m = _lib()
    assert m.sett_sha256() == hashlib.sha256(
        (ROT / "deploy/staging/m14_fasit.py").read_bytes()).hexdigest()


def _kjor(m):
    from db.pg import koble
    tenanter = 0

    def sveip():
        nonlocal tenanter
        from drift import fakturasveip
        v = koble(SVEIP_DSN)
        try:
            r = fakturasveip.kjor(v)
        finally:
            v.close()
        assert not r.feilet and not r.hoppet_over, r
        tenanter = r.tenanter

    rt = koble(DSN)
    try:
        kjoring = m.kjor_sett(m.ny_runde(), rt, sveip)
    finally:
        rt.close()
    return kjoring, tenanter


@pg
@roller
def test_fasiten_er_lik_lokalt(migrator, miljo):  # noqa: F811
    """HELE settet gjennom hele kjeden lokalt. DENNE er «likt lokalt»."""
    m = _lib()
    kjoring, _ = _kjor(m)
    avvik = []
    for rolle, d in sorted(kjoring.items()):
        for akse in m.AKSER:
            avvik += [f"{rolle}/{akse}: {a}" for a in d["avvik"][akse]]
    assert not avvik, "avvik mot fasit:\n  " + "\n  ".join(avvik)


@pg
@roller
def test_artefaktet_bestar_sitt_eget_skjema(migrator, miljo):  # noqa: F811
    """Artefaktet CI bygger av en lokal kjøring må passere NØYAKTIG de
    portene staging-artefaktet passerer."""
    from manifestskjema import (_sjekk_grenser, m14_bevisrot_sha256,
                                valider_artefaktformat)
    m = _lib()
    kjoring, tenanter = _kjor(m)
    art = m.artefakt(kjoring, "lokal", "2026-09-16T00:00:00+00:00", 12,
                     tenanter, m14_bevisrot_sha256())
    assert art["bestatt"] is True, art["avvik"]
    assert valider_artefaktformat(art, "m14-fasit-v1") == []
    assert _sjekk_grenser("m14-fasit-v1", art) == []

    annen = dict(art, oppsett=dict(art["oppsett"], sett_sha256="0" * 64))
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m14-fasit-v1", annen))
    # Ett avvik feller punktet — på hver av de tre aksene.
    for akse in m.AKSER:
        rodt = dict(art, maalt=dict(art["maalt"], **{akse: 1}))
        assert _sjekk_grenser("m14-fasit-v1", rodt), \
            f"porten så ikke {akse}"
    # Et krympet eller utvidet sett er et annet sett.
    for delta in (-1, +1):
        annet = dict(art, maalt=dict(
            art["maalt"],
            fakturaer=art["maalt"]["fakturaer"] + delta))
        assert _sjekk_grenser("m14-fasit-v1", annet), \
            f"porten så ikke et sett med {delta:+d} faktura"
    umalt = dict(art, maalt=dict(art["maalt"], sveipetid_ms=0))
    assert any("tok tiden" in f
               for f in _sjekk_grenser("m14-fasit-v1", umalt))
    tom = dict(art, maalt=dict(art["maalt"], sveip_tenanter=1))
    assert any("gjøre ingenting" in f
               for f in _sjekk_grenser("m14-fasit-v1", tom))
