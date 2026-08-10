"""PR-013 CP2: `metadata`-seksjonen + strukturell nøytralitet.

`metadata` bærer semantikkfrie visningsfelt og FJERNES før policyen når motoren
(v4 §4). Nøytraliteten er dermed STRUKTURELL, ikke grep-bevist: et metadata-felt
kan aldri påvirke en beslutning fordi `evaluate` stripper det ved den ene
inngangen. CI-porten: et metadata-felt som likevel når motorobjektet → rødt.
"""
from pathlib import Path

import yaml

from policy_validator import engine
from policy_validator.engine import EvaluationContext, _motorpolicy
from policy_validator.schema import valider_policy

_ROT = Path(__file__).resolve().parents[3]


def _malpolicy() -> dict:
    return yaml.safe_load(
        (_ROT / "policies" / "bransjemal-tjenestebedrift.yaml").read_text(
            encoding="utf-8"))


def _en_handling(policy: dict) -> str:
    return (policy.get("handlinger") or [{}])[0].get("id", "faktura.bokfor")


def test_skjema_godtar_med_og_uten_metadata():
    p = _malpolicy()
    assert valider_policy(p) == [], "malpolicyen skal validere som den er"
    p2 = dict(p)
    p2["metadata"] = {"visningskode": "grønn", "notat": "intern",
                      "beskrivelser": {"faktura.bokfor": "Bokfør faktura"}}
    assert valider_policy(p2) == [], "metadata-seksjonen skal godtas"


def test_motorpolicy_stripper_metadata():
    p = {"a": 1, "metadata": {"x": 2}}
    ut = _motorpolicy(p)
    assert "metadata" not in ut and ut["a"] == 1
    # Uten metadata: uendret (samme objekt, ingen unødig kopi).
    q = {"a": 1}
    assert _motorpolicy(q) is q


def test_metadata_naar_ALDRI_motorobjektet(monkeypatch):
    """CI-porten: fjern strippingen, og denne testen blir rød. `_evaluer`
    (motorens beslutningsvei) skal aldri se en `metadata`-nøkkel."""
    p = _malpolicy()
    # Et metadata-innhold som ETTERLIGNER semantiske felt — hadde motoren lest
    # det, kunne det påvirket en beslutning. Det skal den aldri få sjansen til.
    p["metadata"] = {"handlinger": [{"id": "gift.utbetaling", "modus": "auto"}],
                     "menneskelig_overstyring": {"godkjennbare": []}}
    sett = {}
    orig = engine._evaluer

    def spion(policy, *a, **k):
        sett["har_metadata"] = isinstance(policy, dict) and "metadata" in policy
        return orig(policy, *a, **k)

    monkeypatch.setattr(engine, "_evaluer", spion)
    engine.evaluate(p, EvaluationContext(tenant_id="t", aktor_rolle="agent",
                                         autentisert=True, kilde="api_token"),
                    {"handling": _en_handling(p)})
    assert sett.get("har_metadata") is False, \
        "motoren mottok metadata — nøytraliteten er ikke strukturell"


def test_metadata_paavirker_ikke_beslutningen():
    p = _malpolicy()
    ctx = EvaluationContext(tenant_id="t", aktor_rolle="agent",
                            autentisert=True, kilde="api_token")
    ev = {"handling": _en_handling(p)}
    uten = engine.evaluate(p, ctx, dict(ev))
    p_meta = dict(p)
    p_meta["metadata"] = {"notat": "endret", "visningskode": "rød"}
    med = engine.evaluate(p_meta, ctx, dict(ev))
    assert uten.beslutning == med.beslutning
    assert [g.kode for g in uten.begrunnelse] == [g.kode for g in med.begrunnelse]
