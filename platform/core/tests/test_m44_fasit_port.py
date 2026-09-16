"""«Likt lokalt»-leddet for M-44s fasitartefakt — STÅENDE måling.

Det samme settet (`deploy/staging/m44_fasit.py`) som staging-artefaktet
drives av, kjøres her gjennom de EKTE dørene lokalt: kundens dører som
runtime (mottaker, kontakt kryptert som API-et, samtykke, kampanje, plan,
avlysning, deaktivering, grense strammet), kampanjesveipen som
SVEIPEROLLEN, og flaten lest gjennom `api.kampanje.svar_for`.

MUTASJONENE SOM FELLER DENNE (hver kjørt som eierrollen, hver på sin
navngitte rad):
  1. `> g.samtykke_gyldig_dogn` → `>=`: «utlopt_kant_365» blir et funn.
  2. `f.n > g.maks_per_periode` → `>=`: «paa_frekvenskanten_2» blir et
     funn.
  3. `k.status <> 'avlyst'` fjernet i `planlagt`: «i_avlyst_kampanje»
     (samtykke 400 døgn, kampanjen avlyst) blir `samtykke_utlopt`.
  4. `samtykke_registrert`-evidensen omdøpt: køen riktig, kjeden mangler.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

ROT = Path(__file__).resolve().parents[3]
SVEIP_DSN = os.environ.get("DISPONIT_TEST_KAMPANJESVEIP_DSN")

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN),
                        reason="test-DSN ikke satt")
roller = pytest.mark.skipif(not SVEIP_DSN,
                            reason="KAMPANJESVEIP-DSN ikke satt")


def _last(navn: str, fil: str):
    spec = importlib.util.spec_from_file_location(navn, ROT / fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    return _last("m44_fasit", "deploy/staging/m44_fasit.py")


def _rt():
    from db.pg import koble
    return koble(DSN)


def test_settet_er_fasiten():
    """Settet ER kantene — og grensene er lest av det."""
    m = _lib()
    sett = dict(m.bygg_sett())
    med = sett["med_grense"]
    dager = {r[0]: r[1][0][1] for r in med}
    g = m.GRENSE_ETTER["gyldig"]
    # Utløpskanten: nøyaktig på (ikke funn) og én over (funn).
    assert dager["utlopt_kant_365"] == -g and dager["utlopt_kant_366"] == -g - 1
    assert "samtykke_utlopt" in dict((r[0], r[4]) for r in med)["utlopt_kant_366"]
    assert not dict((r[0], r[4]) for r in med)["utlopt_kant_365"]
    # Frekvenskanten: nøyaktig på taket (ikke funn), én over (funn).
    plan = {r[0]: len(r[2]) for r in med}
    assert plan["paa_frekvenskanten_2"] == m.GRENSE_ETTER["maks"]
    assert plan["over_frekvens_3"] == m.GRENSE_ETTER["maks"] + 1
    assert plan["over_frekvens_3"] <= m.GRENSE_FOR["maks"], "døra ville nektet"
    # Trukket, avlyst kampanje, fremtidig kampanje, deaktivert, bekreftet.
    for merke in ("trukket_etter_plassering", "i_avlyst_kampanje",
                  "i_fremtidig_kampanje", "deaktivert_etterpaa",
                  "gitt_saa_bekreftet"):
        assert merke in dager
    uten = sett["uten_grense"]
    assert sum(1 for r in uten if "ingen_grense" in r[4]) == 2
    from manifestskjema import KRAVGRENSER
    kg = KRAVGRENSER["m44-fasit-v1"]
    assert sum(len(r) for r in sett.values()) == kg["mottakere_eksakt"]
    assert sum(len(r[4]) for rader in sett.values() for r in rader) \
        == kg["ventede_funn_eksakt"]
    assert sum(sum(m.forventet_evidens(r, k == "med_grense").values())
               for k, r in sett.items()) == kg["evidenshendelser_eksakt"]


def test_settet_er_bundet_til_bytene_ikke_til_navnet_sitt():
    import hashlib
    m = _lib()
    assert m.sett_sha256() == hashlib.sha256(
        (ROT / "deploy/staging/m44_fasit.py").read_bytes()).hexdigest()


def _kjor(m):
    from db.pg import koble
    tenanter = 0

    def sveip():
        nonlocal tenanter
        from drift import kampanjesveip
        v = koble(SVEIP_DSN)
        try:
            r = kampanjesveip.kjor(v)
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
    from manifestskjema import (_sjekk_grenser, m44_bevisrot_sha256,
                                valider_artefaktformat)
    m = _lib()
    kjoring, tenanter = _kjor(m)
    art = m.artefakt(kjoring, "lokal", "2026-09-16T00:00:00+00:00", 12,
                     tenanter, m44_bevisrot_sha256())
    assert art["bestatt"] is True, art["avvik"]
    assert valider_artefaktformat(art, "m44-fasit-v1") == []
    assert _sjekk_grenser("m44-fasit-v1", art) == []

    annen = dict(art, oppsett=dict(art["oppsett"], sett_sha256="0" * 64))
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m44-fasit-v1", annen))
    # Ett avvik feller punktet — på hver av de fire aksene.
    for akse in m.AKSER:
        rodt = dict(art, maalt=dict(art["maalt"], **{akse: 1}))
        assert _sjekk_grenser("m44-fasit-v1", rodt), \
            f"porten så ikke {akse}"
    # Et krympet eller utvidet sett er et annet sett.
    for delta in (-1, +1):
        annet = dict(art, maalt=dict(
            art["maalt"],
            mottakere=art["maalt"]["mottakere"] + delta))
        assert _sjekk_grenser("m44-fasit-v1", annet), \
            f"porten så ikke et sett med {delta:+d} mottaker"
    umalt = dict(art, maalt=dict(art["maalt"], sveipetid_ms=0))
    assert any("tok tiden" in f
               for f in _sjekk_grenser("m44-fasit-v1", umalt))
    tom = dict(art, maalt=dict(art["maalt"], sveip_tenanter=1))
    assert any("gjøre ingenting" in f
               for f in _sjekk_grenser("m44-fasit-v1", tom))
