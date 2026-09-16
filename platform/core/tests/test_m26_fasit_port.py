"""«Likt lokalt»-leddet for M-26s fasitartefakt — STÅENDE måling.

Det samme settet (`deploy/staging/m26_fasit.py`) som staging-artefaktet
drives av, kjøres her gjennom de EKTE dørene lokalt: kundens dører som
runtime (terskler, klausul, produkt, pris i versjoner, deaktivering,
terskel strammet), prisboksveipen som SVEIPEROLLEN, og flaten lest
gjennom `api.prisbok.svar_for`.

MUTASJONENE SOM FELLER DENNE (hver kjørt som eierrollen, hver på sin
navngitte rad):
  1. `g.gyldig_til <= p_dag + v_t.utlop_varsel_dogn` → `<`:
     «utloper_kant_30» er ikke lenger et funn.
  2. `AND p.aktiv` fjernet i `pris_utloper_snart`-grenen:
     «deaktivert_utloper» blir et funn.
  3. `AND p.aktiv` fjernet i `ingen_terskel`-grenen:
     «deaktivert_uten_terskel» blir et funn.
  4. `pris.satt`-evidensen omdøpt: boka riktig, kjeden mangler.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

ROT = Path(__file__).resolve().parents[3]
SVEIP_DSN = os.environ.get("DISPONIT_TEST_PRISBOKSVEIP_DSN")

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN),
                        reason="test-DSN ikke satt")
roller = pytest.mark.skipif(not SVEIP_DSN,
                            reason="PRISBOKSVEIP-DSN ikke satt")


def _last(navn: str, fil: str):
    spec = importlib.util.spec_from_file_location(navn, ROT / fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    return _last("m26_fasit", "deploy/staging/m26_fasit.py")


def _rt():
    from db.pg import koble
    return koble(DSN)


def test_settet_er_fasiten():
    """Settet ER kantene — og grensene er lest av det."""
    m = _lib()
    sett = dict(m.bygg_sett())
    med = sett["med_terskel"]
    rad = {r[0]: r for r in med}
    v = m.TERSKLER_ETTER["varsel"]
    # Utløpskanten: neste versjon fra v+1 gir gyldig_til = i dag + v
    # (funn, `<=`); fra v+2 gir i dag + v+1 (ikke funn).
    assert rad["utloper_kant_30"][1][1][1] == v + 1
    assert rad["utloper_kant_31"][1][1][1] == v + 2
    assert rad["utloper_kant_30"][3] == {"pris_utloper_snart"}
    assert rad["utloper_kant_31"][3] == set()
    assert m._gjeldende(rad["utloper_kant_30"][1]) == (1, v)
    assert m._gjeldende(rad["utloper_kant_31"][1]) == (1, v + 1)
    # Prisene ble satt under den ROMSLIGERE terskelen — kanten hører til
    # den strammede.
    assert m.TERSKLER_FOR["varsel"] < v
    # I dag, om fem, deaktivert, uten pris, kun framtidig, tre versjoner.
    assert m._gjeldende(rad["utloper_i_dag"][1]) == (1, 0)
    assert rad["deaktivert_utloper"][2] == "deaktiver" and not rad["deaktivert_utloper"][3]
    assert rad["uten_pris"][1] == [] and not rad["uten_pris"][3]
    assert m._gjeldende(rad["kun_framtidig_pris"][1]) == (None, None)
    assert len(rad["tre_versjoner"][1]) == 3
    uten = sett["uten_terskel"]
    assert sum(1 for r in uten if "ingen_terskel" in r[3]) == 2
    assert any(r[2] == "deaktiver" and not r[3] for r in uten)
    from manifestskjema import KRAVGRENSER
    kg = KRAVGRENSER["m26-fasit-v1"]
    assert sum(len(r) for r in sett.values()) == kg["produkter_eksakt"]
    assert sum(len(r[3]) for rader in sett.values() for r in rader) \
        == kg["ventede_funn_eksakt"]
    assert sum(sum(m.forventet_evidens(r, k == "med_terskel").values())
               for k, r in sett.items()) == kg["evidenshendelser_eksakt"]


def test_settet_er_bundet_til_bytene_ikke_til_navnet_sitt():
    import hashlib
    m = _lib()
    assert m.sett_sha256() == hashlib.sha256(
        (ROT / "deploy/staging/m26_fasit.py").read_bytes()).hexdigest()


def _kjor(m):
    from db.pg import koble
    tenanter = 0

    def sveip():
        nonlocal tenanter
        from drift import prisboksveip
        v = koble(SVEIP_DSN)
        try:
            r = prisboksveip.kjor(v)
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
    from manifestskjema import (_sjekk_grenser, m26_bevisrot_sha256,
                                valider_artefaktformat)
    m = _lib()
    kjoring, tenanter = _kjor(m)
    art = m.artefakt(kjoring, "lokal", "2026-09-16T00:00:00+00:00", 12,
                     tenanter, m26_bevisrot_sha256())
    assert art["bestatt"] is True, art["avvik"]
    assert valider_artefaktformat(art, "m26-fasit-v1") == []
    assert _sjekk_grenser("m26-fasit-v1", art) == []

    annen = dict(art, oppsett=dict(art["oppsett"], sett_sha256="0" * 64))
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m26-fasit-v1", annen))
    # Ett avvik feller punktet — på hver av de tre aksene.
    for akse in m.AKSER:
        rodt = dict(art, maalt=dict(art["maalt"], **{akse: 1}))
        assert _sjekk_grenser("m26-fasit-v1", rodt), \
            f"porten så ikke {akse}"
    # Et krympet eller utvidet sett er et annet sett.
    for delta in (-1, +1):
        annet = dict(art, maalt=dict(
            art["maalt"],
            produkter=art["maalt"]["produkter"] + delta))
        assert _sjekk_grenser("m26-fasit-v1", annet), \
            f"porten så ikke et sett med {delta:+d} produkt"
    umalt = dict(art, maalt=dict(art["maalt"], sveipetid_ms=0))
    assert any("tok tiden" in f
               for f in _sjekk_grenser("m26-fasit-v1", umalt))
    tom = dict(art, maalt=dict(art["maalt"], sveip_tenanter=1))
    assert any("gjøre ingenting" in f
               for f in _sjekk_grenser("m26-fasit-v1", tom))
