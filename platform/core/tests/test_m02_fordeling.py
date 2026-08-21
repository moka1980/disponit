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


def _lib():
    spec = importlib.util.spec_from_file_location(
        "m02_fordeling", ROT / "deploy/staging/m02_fordeling.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


@pg
def test_fordelingen_er_lik_lokalt(klient, policy, token, migrator):
    """Hele settet gjennom den EKTE beslutningsveien lokalt: hver
    kategori dømmes per svar (fail-closed i driveren), radene leses av
    revisjonsloggen via idempotensnøklene, og artefaktet — bygget av
    NØYAKTIG samme funksjon som staging-leddet bruker — består både det
    lukkede skjemaet og grensene. Én rad fjernet feller det."""
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    m = _lib()
    tok, _ = token()
    runde = secrets.token_hex(4)

    def _post(e, nokkel):
        r = post(klient, policy, e, tok, nokkel=nokkel)
        return r.status_code, (r.json().get("beslutning")
                               if r.status_code == 200 else None)

    def _tillat(ressurs):
        return hendelse(policy, ressurs=ressurs)

    def _uten(handling, ressurs):
        return hendelse_uten_attestasjoner(ressurs=ressurs,
                                           handling=handling)

    def _tukle(e):
        # snudd UTEN ny signering — signaturporten skal felle den, og
        # nettopp det avtrykket er STOPP-kategorien.
        e["attestasjoner"]["ingen_aktiv_tvist"]["resultat"] = False
        return e

    talt = m.kjor_sett(runde, _post, _tillat, _uten, _tukle)
    assert talt == m.FORDELING
    migrator.execute("RESET ROLE")
    migrator.execute("SELECT set_config('disponit.tenant', %s, false)",
                     (TENANT,))
    rader = migrator.execute(
        "SELECT id, beslutning FROM revisjonslogg WHERE tenant=%s"
        "  AND idempotency_key LIKE %s",
        (TENANT, f"m02f-{runde}-%")).fetchall()
    migrator.rollback()
    art = m.artefakt([(r[0], r[1]) for r in rader], TENANT, "lokal",
                     "2026-08-21T00:00:00+00:00")
    assert art["bestatt"] is True
    assert valider_artefaktformat(art, "m02-fordeling-v1") == []
    assert _sjekk_grenser("m02-fordeling-v1", art) == []
    # Negative porter: én rad borte → fordelingen spriker; en beslutning
    # byttet → re-regningen feller den; gjentatt loggpost → én hendelse
    # er én rad.
    amputert = dict(art, rader=art["rader"][1:],
                    maalt=dict(art["maalt"]))
    assert any("fasiten" in f or "krever >=" in f
               for f in _sjekk_grenser("m02-fordeling-v1", amputert))
    annen = "TILLAT" if art["rader"][0][1] != "TILLAT" else "STOPP"
    byttet = dict(art, rader=[[art["rader"][0][0], annen]]
                  + [list(r) for r in art["rader"][1:]])
    assert any("fasiten" in f
               for f in _sjekk_grenser("m02-fordeling-v1", byttet))
    dublert = dict(art, rader=[list(art["rader"][0])]
                   + [list(r) for r in art["rader"][:-1]])
    assert any("gjentar" in f
               for f in _sjekk_grenser("m02-fordeling-v1", dublert))
