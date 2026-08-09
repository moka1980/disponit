"""PR-012 CP3: policy-skjema `menneskelig_overstyring` — lukket (grunnkode,
handling)-mapping. Rene skjema-/semantikktester (ingen DB)."""
import copy
from pathlib import Path

import yaml

from policy_validator.schema import valider_policy

_BASE = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "policies"
     / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))


def _med(mo):
    p = copy.deepcopy(_BASE)
    p["menneskelig_overstyring"] = mo
    return p


_GYLDIG = {
    "godkjennbare": [
        {"grunnkode": "belop_over_grense", "handling": "faktura.bokfor",
         "belop_maks": "50000.00", "valuta": "NOK"},
        {"grunnkode": "frekvensgrense_naadd", "handling": "purring.send"},
    ],
    "krever_rolle": "okonomiansvarlig",
    "krever_fire_oyne": True,
    "begrunnelse_pakrevd": True,
}


def _feil(mo):
    return valider_policy(_med(mo))


def test_gyldig_menneskelig_overstyring_passerer():
    assert valider_policy(_med(_GYLDIG)) == []


def test_fravaer_er_gyldig_deny_by_default():
    # Optional: en policy uten feltet er gyldig (og gir ingen godkjenning).
    assert valider_policy(_BASE) == []


def test_ukjent_handling_avvises():
    mo = copy.deepcopy(_GYLDIG)
    mo["godkjennbare"][0]["handling"] = "finnes.ikke"
    assert any("ukjent handling" in f for f in _feil(mo))


def test_ikke_godkjennbar_grunnkode_avvises():
    for gk in ("teknisk_feil", "manglende_data", "motor_exception"):
        mo = copy.deepcopy(_GYLDIG)
        mo["godkjennbare"][0]["grunnkode"] = gk
        assert any("kan aldri godkjennes" in f for f in _feil(mo)), gk


def test_ukjent_rolle_avvises():
    mo = copy.deepcopy(_GYLDIG)
    mo["krever_rolle"] = "finnesikke"
    assert any("ukjent rolle" in f for f in _feil(mo))


def test_duplisert_par_avvises():
    mo = copy.deepcopy(_GYLDIG)
    mo["godkjennbare"].append(dict(mo["godkjennbare"][0]))
    assert any("duplisert" in f for f in _feil(mo))


def test_belop_maks_krever_valuta():
    mo = copy.deepcopy(_GYLDIG)
    del mo["godkjennbare"][0]["valuta"]        # belop_maks uten valuta
    feil = _feil(mo)
    assert feil and any("valuta" in f or "skjema" in f for f in feil)


def test_lukket_ingen_ekstra_felt():
    mo = copy.deepcopy(_GYLDIG)
    mo["fri_passthrough"] = True               # additionalProperties: false
    assert any("skjema" in f for f in _feil(mo))
    mo2 = copy.deepcopy(_GYLDIG)
    mo2["godkjennbare"][0]["ekstra"] = 1
    assert any("skjema" in f for f in _feil(mo2))
