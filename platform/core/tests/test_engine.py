"""Tester for policy-validatoren.

Hver test er merket med hvilket akseptansekriterium fra prototype v7.2
den beviser. NEGATIVE tester (at handling utenfor policy faktisk
stoppes) er obligatoriske i CI-port 2 og kan ikke fjernes.
"""
from pathlib import Path

import pytest
import yaml

from policy_validator.audit import input_hash, lag_loggpost
from policy_validator.engine import STOPP, TILLAT, UNNTAK, evaluate
from policy_validator.schema import valider_policy

from .conftest import POLICIES  # repo-rot/policies


@pytest.fixture(scope="module")
def tjeneste():
    return yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml").read_text())


@pytest.fixture(scope="module")
def netthandel():
    return yaml.safe_load(
        (POLICIES / "bransjemal-netthandel.yaml").read_text())


def hendelse(**over):
    """Gyldig grunnhendelse for faktura.bokfor i tjenestemalen."""
    e = {
        "handling": "faktura.bokfor",
        "aktor_rolle": "agent",
        "belop": 12000,
        "valuta": "NOK",
        "dataklasser": ["finansiell"],
        "vilkaar": {"dublettsjekk": True, "leverandor_i_register": True,
                    "mva_validert": True},
    }
    e.update(over)
    return e


# ---------- Skjemavalidering (utrullingsport: policy er også deploy) ------

def test_alle_bransjemaler_bestar_skjema():
    for fil in POLICIES.glob("bransjemal-*.yaml"):
        policy = yaml.safe_load(fil.read_text())
        assert valider_policy(policy) == [], f"{fil.name} feiler skjema"


def test_skjema_avviser_irreversibel_uten_rammer():
    policy = {"schema_version": "0.1", "meta": {}, "roller": [{"id": "agent"}],
              "unntak": {},
              "handlinger": [{"id": "farlig.slett", "modus": "auto",
                              "tillatt_for": ["agent"], "reversibel": False}]}
    feil = valider_policy(policy)
    assert any("irreversibel" in f for f in feil)


# ---------- Positive: normalflyt tillates (M-1: automatisk normaldrift) ---

def test_gyldig_faktura_tillates(tjeneste):
    d = evaluate(tjeneste, hendelse())
    assert d.beslutning == TILLAT
    assert d.policy_id.startswith("tjenestebedrift-no-v0.1/faktura.bokfor")
    assert d.begrunnelse  # M-1: begrunnelse alltid med


def test_vilkaar_med_terskel(netthandel):
    d = evaluate(netthandel, {
        "handling": "lager.bestill_pafyll", "aktor_rolle": "agent",
        "belop": 20000, "vilkaar": {
            "leverandor_i_register": True, "pris_innen_avtale": True,
            "prognose_konfidens_min": 0.91}})
    assert d.beslutning == TILLAT


# ---------- NEGATIVE: blokkerte handlinger utføres aldri (M-1 aksept) -----

def test_deny_by_default_ukjent_handling(tjeneste):
    d = evaluate(tjeneste, hendelse(handling="server.slett_alt"))
    assert d.beslutning == UNNTAK
    assert d.unntak_kategori == "ukjent"


def test_over_belopsgrense_stoppes(tjeneste):
    d = evaluate(tjeneste, hendelse(belop=25001))
    assert d.beslutning == UNNTAK
    assert d.unntak_kategori == "over_grense"


def test_grense_er_inklusiv(tjeneste):
    assert evaluate(tjeneste, hendelse(belop=25000)).beslutning == TILLAT


def test_alltid_stopp_kan_aldri_tillates(tjeneste):
    d = evaluate(tjeneste, {"handling": "epost.send_ny_mottaker",
                            "aktor_rolle": "agent"})
    assert d.beslutning == UNNTAK


def test_feil_rolle_stoppes(tjeneste):
    d = evaluate(tjeneste, hendelse(aktor_rolle="konsulent"))
    assert d.beslutning != TILLAT


def test_manglende_vilkaar_gir_unntak_ikke_gjetting(tjeneste):
    e = hendelse()
    del e["vilkaar"]["mva_validert"]  # mangler helt — skal aldri antas True
    d = evaluate(tjeneste, e)
    assert d.beslutning == UNNTAK
    assert d.unntak_kategori == "manglende_data"


def test_vilkaar_false_stopper(tjeneste):
    d = evaluate(tjeneste, hendelse(vilkaar={"dublettsjekk": False,
                                             "leverandor_i_register": True,
                                             "mva_validert": True}))
    assert d.beslutning != TILLAT


def test_ulovlig_dataklasse_stoppes(tjeneste):
    d = evaluate(tjeneste, hendelse(dataklasser=["finansiell", "sensitiv"]))
    assert d.beslutning != TILLAT


def test_betaling_utenfor_tidsvindu(tjeneste):
    d = evaluate(tjeneste, {
        "handling": "betaling.utfor", "aktor_rolle": "agent", "belop": 5000,
        "tidspunkt": "2026-08-02T12:00:00",  # søndag
        "vilkaar": {"faktura_godkjent": True, "konto_verifisert": True,
                    "svindelsjekk_bestatt": True}})
    assert d.beslutning == STOPP  # ved_brudd: frys
    assert d.effekt == "frys"


def test_svindelsjekk_feiler_gir_frys(tjeneste):
    d = evaluate(tjeneste, {
        "handling": "betaling.utfor", "aktor_rolle": "agent", "belop": 5000,
        "tidspunkt": "2026-08-03T10:00:00",  # mandag
        "vilkaar": {"faktura_godkjent": True, "konto_verifisert": True,
                    "svindelsjekk_bestatt": False}})
    assert d.beslutning == STOPP and d.effekt == "frys"


def test_uleselig_tidspunkt_gir_stopp_ikke_antakelse(tjeneste):
    d = evaluate(tjeneste, {
        "handling": "betaling.utfor", "aktor_rolle": "agent", "belop": 5000,
        "tidspunkt": "i går en gang",
        "vilkaar": {"faktura_godkjent": True, "konto_verifisert": True,
                    "svindelsjekk_bestatt": True}})
    assert d.beslutning != TILLAT


# ---------- M-2: logg med input-hash, aktør, policy-ID, begrunnelse -------

def test_loggpost_har_alle_pakrevde_felt(tjeneste):
    e = hendelse()
    d = evaluate(tjeneste, e)
    post = lag_loggpost(d, e, tjeneste)
    for felt in ("ts", "input_hash", "aktor", "policy_id", "beslutning",
                 "begrunnelse"):
        assert post[felt], f"loggfelt '{felt}' mangler/tomt"


def test_input_hash_er_deterministisk():
    e1 = hendelse()
    e2 = hendelse()
    assert input_hash(e1) == input_hash(e2)
    assert input_hash(hendelse(belop=1)) != input_hash(e1)
