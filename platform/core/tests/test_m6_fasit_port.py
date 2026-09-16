"""«Likt lokalt»-leddet for M-6s fasitartefakt — STÅENDE måling.

Det samme settet (`deploy/staging/m6_fasit.py`) som staging-artefaktet
drives av, kjøres her gjennom de EKTE dørene lokalt: kilden, utkastet og
slettingen som runtime, inntaket og utsendingen som PLANARBEIDEREN, Graph
byttet ut med den riggede transporten. Alle fire fasitene måles: inntaket,
broen til M-17, svaret og evidenskjeden.

MUTASJONENE SOM FELLER DENNE (hver kjørt, hver på sin navngitte rad):
  1. `ON CONFLICT … DO NOTHING` → «nye» teller duplikatet, og innboksen
     får sju rader.
  2. Broen slått av (`DISPONIT_EPOST_TIL_KUNDESERVICE=av`): null
     henvendelser.
  3. `m6_send_svaret` uten `kilde_kan_svare`-sjekken: tenanten uten
     sendescope får «sendes», og Graph får en POST.
  4. `m6_svar_sendt` uten evidens: svaret gikk, kjeden mangler
     `epost.svar_sendt`.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

ROT = Path(__file__).resolve().parents[3]
PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN and PLAN_DSN),
                        reason="test-DSN/PLAN_DSN ikke satt")


def _last(navn: str, fil: str):
    spec = importlib.util.spec_from_file_location(navn, ROT / fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    return _last("m6_fasit", "deploy/staging/m6_fasit.py")


@pytest.fixture
def m365(monkeypatch):
    """M365-konfigurasjonen må FINNES for at innhenteren starter; verdiene
    brukes aldri — veksleren er rigget. Broen og inntaket må være PÅ."""
    monkeypatch.setenv("DISPONIT_M365_CLIENT_ID", "fasit")
    monkeypatch.setenv("DISPONIT_M365_CLIENT_SECRET", "fasit")
    monkeypatch.setenv("DISPONIT_M365_TENANT", "common")
    monkeypatch.delenv("DISPONIT_EPOST_INNTAK", raising=False)
    monkeypatch.delenv("DISPONIT_EPOST_TIL_KUNDESERVICE", raising=False)


def test_settet_er_fasiten():
    """Settet ER kantene — og grensene er lest av det."""
    m = _lib()
    assert m.bygg_sett() == [("med_sendescope", True), ("uten_sendescope", False)]
    merker = [r[0] for r in m.SIDE_1 + m.SIDE_2 + m.SIDE_3]
    assert merker.count("m1") == 2 and merker.count("m6") == 2   # duplikater
    assert any(r[4] for r in m.SIDE_1), "ingen @removed"
    assert any(not r[1] for r in m.SIDE_1), "ingen uten avsender"
    assert any(not r[3] for r in m.SIDE_1), "ingen uten kropp"
    assert any(r[2] for r in m.SIDE_1), "ingen med vedlegg"
    assert m.SLETTES in m.MELDINGER and m.SVARES_PAA in m.MELDINGER
    assert m.SLETTES != m.SVARES_PAA
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m6-fasit-v1"]
    n = len(m.bygg_sett())
    assert len(m.MELDINGER) * n == g["meldinger_eksakt"]
    assert m.VENTET_RUNDE_1["nye"] * n == g["nye_runde1_eksakt"]
    assert m.VENTET_RUNDE_2["nye"] * n == g["nye_runde2_eksakt"]
    assert len(m.MELDINGER) * n == g["henvendelser_eksakt"]
    assert sum(1 for _r, k in m.bygg_sett() if k) == g["sendt_eksakt"]
    assert sum(sum(sum(v.values()) for v in m.forventet_evidens(k).values())
               for _r, k in m.bygg_sett()) == g["evidenshendelser_eksakt"]


def test_settet_er_bundet_til_bytene_ikke_til_navnet_sitt():
    import hashlib
    m = _lib()
    assert m.sett_sha256() == hashlib.sha256(
        (ROT / "deploy/staging/m6_fasit.py").read_bytes()).hexdigest()


def _kjor(m):
    from db.pg import koble
    rt, pa = koble(DSN), koble(PLAN_DSN)
    try:
        return m.kjor_sett(m.ny_runde(), rt, pa)
    finally:
        rt.close()
        pa.close()


@pg
def test_fasiten_er_lik_lokalt(migrator, m365):  # noqa: F811
    """HELE settet gjennom hele kjeden lokalt. DENNE er «likt lokalt»."""
    m = _lib()
    kjoring = _kjor(m)
    avvik = []
    for rolle, d in sorted(kjoring.items()):
        for akse in m.AKSER:
            avvik += [f"{rolle}/{akse}: {a}" for a in d["avvik"][akse]]
    assert not avvik, "avvik mot fasit:\n  " + "\n  ".join(avvik)


@pg
def test_artefaktet_bestar_sitt_eget_skjema(migrator, m365):  # noqa: F811
    from manifestskjema import (_sjekk_grenser, m6_bevisrot_sha256,
                                valider_artefaktformat)
    m = _lib()
    kjoring = _kjor(m)
    art = m.artefakt(kjoring, "lokal", "2026-09-16T00:00:00+00:00",
                     m6_bevisrot_sha256())
    assert art["bestatt"] is True, art["avvik"]
    assert valider_artefaktformat(art, "m6-fasit-v1") == []
    assert _sjekk_grenser("m6-fasit-v1", art) == []
    annen = dict(art, oppsett=dict(art["oppsett"], sett_sha256="0" * 64))
    assert any("sett_sha256" in f for f in _sjekk_grenser("m6-fasit-v1", annen))
    for akse in m.AKSER:
        rodt = dict(art, maalt=dict(art["maalt"], **{akse: 1}))
        assert _sjekk_grenser("m6-fasit-v1", rodt), f"porten så ikke {akse}"
    for delta in (-1, +1):
        annet = dict(art, maalt=dict(art["maalt"],
                                     meldinger=art["maalt"]["meldinger"] + delta))
        assert _sjekk_grenser("m6-fasit-v1", annet)
    umalt = dict(art, maalt=dict(art["maalt"], innhentingstid_ms=0))
    assert any("tok tiden" in f for f in _sjekk_grenser("m6-fasit-v1", umalt))
    treg = dict(art, maalt=dict(art["maalt"], innhentingstid_ms=29_000,
                                meldinger=1))
    assert any("per melding" in f for f in _sjekk_grenser("m6-fasit-v1", treg))
