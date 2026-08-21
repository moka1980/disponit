"""«Likt lokalt»-leddet for m02-fordelingsartefaktet — STÅENDE måling.

Det samme settet (`deploy/staging/m02_fordeling.bygg_sett`) som
staging-artefaktet drives av, kjøres her gjennom den lokale
beslutningsveien, og fordelingen måles i revisjonsloggen. Da er «det
syntetiske datasettet er likt lokalt» en port CI feller ved hver
kjøring — ikke et minne fra en runde (m02-aksept-klarsignalet §3,
premiss korrigert: m01-rundens historiske 180 rader finnes ikke i
prod-basen, målt 2026-08-21 — hele basen hadde null STOPP).
"""
from __future__ import annotations

import importlib.util
import secrets
from pathlib import Path

from .test_api import (  # noqa: F401 — delte fixturer og byggere
    TENANT, app, hendelse, hendelse_uten_attestasjoner, klient,
    malpolicy, migrator, miljo, pg, policy, post, token)

ROT = Path(__file__).resolve().parents[3]


def _last(navn: str, fil: str):
    spec = importlib.util.spec_from_file_location(navn, ROT / fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    return _last("m02_fordeling", "deploy/staging/m02_fordeling.py")


def test_settet_er_fasiten():
    """Settet ER fordelingen 84/3/93 over 180 — deterministisk, og
    grensen i KRAVGRENSER bærer NØYAKTIG samme fasit (porten binder de
    to kildene så de ikke kan gli fra hverandre)."""
    from manifestskjema import KRAVGRENSER
    m = _lib()
    sett = m.bygg_sett()
    assert len(sett) == 180 == sum(m.FORDELING.values())
    talt: dict[str, int] = {}
    for beslutning, _ in sett:
        talt[beslutning] = talt.get(beslutning, 0) + 1
    assert talt == m.FORDELING \
        == KRAVGRENSER["m02-fordeling-v1"]["fordeling_eksakt"]
    assert m.bygg_sett() == sett            # deterministisk


def test_settet_er_bundet_til_bytene_ikke_til_navnet_sitt():
    """«Likt lokalt» er en påstand om SETTET, ikke om summen.

    Radene bærer loggpost-id og beslutning — ikke hendelsene som ble sendt
    inn — og `sett_versjon` er en håndholdt streng. Et staging-ledd på en
    eldre utrulling kunne derfor drive helt andre hendelser til de samme
    84/3/93 og valideres som det samme settet. Bytene er bindingen, og de
    hashes i BEGGE ledd (samme form som datasett_sha256/§1.2).

    MUTASJONEN SOM DREPER DENNE: la porten godta et artefakt uten
    `sett_sha256`, eller slutt å sammenligne med de innsjekkede bytene.
    """
    import hashlib

    from manifestskjema import (M02_SETT_STI, _sjekk_grenser,
                                valider_artefaktformat)
    m = _lib()
    assert m.sett_sha256() == hashlib.sha256(
        M02_SETT_STI.read_bytes()).hexdigest()

    rader = [(i + 1, b) for i, (b, _) in enumerate(m.bygg_sett())]
    art = m.artefakt(rader, "t-test", "lokal", "2026-08-21T00:00:00+00:00")
    assert art["bestatt"] is True
    assert valider_artefaktformat(art, "m02-fordeling-v1") == []
    assert _sjekk_grenser("m02-fordeling-v1", art) == []

    # En driver som ikke er den innsjekkede — samme tall, annet sett.
    annen = dict(art, oppsett=dict(art["oppsett"], sett_sha256="0" * 64))
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m02-fordeling-v1", annen))
    # ... og et artefakt uten bindingen i det hele tatt er umålt, ikke
    # grønt: det lukkede skjemaet krever feltet, og porten sier det selv.
    uten = dict(art, oppsett={k: v for k, v in art["oppsett"].items()
                              if k != "sett_sha256"})
    assert valider_artefaktformat(uten, "m02-fordeling-v1") != []
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m02-fordeling-v1", uten))
