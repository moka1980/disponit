"""Tester v0.2 — hver seksjon mapper til et funn i ChatGPT-review PR-001.
Negative tester er obligatoriske i CI og kan aldri fjernes/svekkes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from .conftest import POLICIES
from policy_validator.audit import lag_loggpost, sikker_beslutning
from policy_validator.engine import (
    STOPP, TILLAT, UNNTAK, Decision, EvaluationContext, MinneTellerLager,
    evaluate, parse_belop)
from policy_validator.schema import valider_policy

NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)  # mandag
CTX = EvaluationContext(tenant_id="t1", aktor_rolle="agent",
                        autentisert=True, kilde="api_token")


@pytest.fixture(scope="module")
def tjeneste():
    return yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml").read_text())


@pytest.fixture(scope="module")
def netthandel():
    return yaml.safe_load((POLICIES / "bransjemal-netthandel.yaml").read_text())


def att(verifikator, ressurs="fak-1", resultat=True, verdi=None, timer=1):
    a = {"verifikator": verifikator, "ressurs_id": ressurs,
         "utloper": (NAA + timedelta(hours=timer)).isoformat()}
    if verdi is not None:
        a["verdi"] = verdi
    else:
        a["resultat"] = resultat
    return a


def hendelse(**over):
    e = {"handling": "faktura.bokfor", "belop": "12000.00", "valuta": "NOK",
         "ressurs_id": "fak-1",
         "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
         "attestasjoner": {
             "dublettsjekk": att("v_regnskap"),
             "leverandor_i_register": att("v_register"),
             "mva_validert": att("v_regnskap")}}
    e.update(over)
    return e


# ---------- Skjema (funn B: formelt JSON Schema) --------------------------

def test_alle_maler_gyldige():
    for f in POLICIES.glob("bransjemal-*.yaml"):
        assert valider_policy(yaml.safe_load(f.read_text())) == [], f.name


def test_ukjente_felter_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    p["hemmelig_bakdor"] = True
    assert any("hemmelig_bakdor" in f for f in valider_policy(p))


def test_tekstlig_frekvens_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    p["handlinger"][3]["grenser"]["frekvens"] = "1 per faktura per 14 dager"
    assert valider_policy(p)


def test_manglende_tidssone_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    del p["tidssone"]
    assert valider_policy(p)


def test_ukjent_iana_tidssone_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    p["tidssone"] = "Norge/Narvik"
    assert any("IANA" in f for f in valider_policy(p))


def test_uregistrert_verifikator_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    p["handlinger"][0]["vilkaar"][0]["verifikator"] = "v_finnes_ikke"
    assert any("uregistrert verifikator" in f for f in valider_policy(p))


def test_feilformet_policy_gir_feilliste_ikke_exception():
    assert valider_policy(None)
    assert valider_policy({"handlinger": "ikke en liste"})
    assert valider_policy({"handlinger": [42]})
    assert valider_policy("bare en streng")


def test_irreversibel_uten_rammer_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    h = p["handlinger"][0]
    del h["grenser"]; del h["vilkaar"]
    h["reversering"] = {"type": "irreversibel"}
    assert any("irreversibel" in f for f in valider_policy(p))


# ---------- Autentisert kontekst (funn: uautentisert rolle) ---------------

def test_uten_kontekst_stopp(tjeneste):
    d = evaluate(tjeneste, None, hendelse(), naa=NAA)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "uautentisert_kontekst"


def test_uautentisert_kontekst_stopp(tjeneste):
    ctx = EvaluationContext("t1", "agent", autentisert=False, kilde="x")
    assert evaluate(tjeneste, ctx, hendelse(), naa=NAA).beslutning == STOPP


def test_rolle_fra_event_ignoreres(tjeneste):
    # Angriper later som daglig_leder i payload — konteksten avgjør
    ctx = EvaluationContext("t1", "konsulent", True, "api_token")
    d = evaluate(tjeneste, ctx, hendelse(aktor_rolle="daglig_leder"), naa=NAA)
    assert d.beslutning != TILLAT


# ---------- Beløp (funn D: Decimal, bool, negativ) ------------------------

def test_parse_belop_grensetilfeller():
    assert parse_belop("12000.00") == Decimal("12000.00")
    assert parse_belop(500) == Decimal("500")
    assert parse_belop(True) is None          # bool er int i Python
    assert parse_belop(False) is None
    assert parse_belop(-1) is None
    assert parse_belop("-0.01") is None
    assert parse_belop("NaN") is None
    assert parse_belop("Infinity") is None
    assert parse_belop("10.999") is None      # > 2 desimaler
    assert parse_belop(10.5) is None          # float avvises for penger
    assert parse_belop("abc") is None


def test_bool_belop_gir_ugyldig_data(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(belop=True), naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "ugyldig_data"


def test_negativt_belop_gir_ugyldig_data(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(belop="-500.00"), naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "ugyldig_data"


def test_over_grense_og_inklusiv(tjeneste):
    assert evaluate(tjeneste, CTX, hendelse(belop="25000.00"),
                    naa=NAA).beslutning == TILLAT
    d = evaluate(tjeneste, CTX, hendelse(belop="25000.01"), naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "over_grense"


# ---------- Dataklasser (funn C: fail-closed) -----------------------------

def test_tom_dataklassifisering_gir_unntak(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(dataklasser=[]), naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "manglende_data"


def test_selvrapportert_kilde_avvises(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(dataklasser_kilde="selvrapportert"),
                 naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "manglende_data"


def test_ulovlig_dataklasse(tjeneste):
    d = evaluate(tjeneste, CTX,
                 hendelse(dataklasser=["finansiell", "sensitiv"]), naa=NAA)
    assert d.beslutning != TILLAT


# ---------- Attestasjoner (funn: selvattestering) -------------------------

def test_gyldig_hendelse_tillates(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(), naa=NAA)
    assert d.beslutning == TILLAT


def test_manglende_attestasjon(tjeneste):
    e = hendelse(); del e["attestasjoner"]["mva_validert"]
    d = evaluate(tjeneste, CTX, e, naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "manglende_data"


def test_ubetrodd_verifikator_stopper(tjeneste):
    e = hendelse()
    e["attestasjoner"]["mva_validert"] = att("v_bank")  # ikke betrodd for mva
    assert evaluate(tjeneste, CTX, e, naa=NAA).beslutning == STOPP


def test_utlopt_attestasjon(tjeneste):
    e = hendelse()
    e["attestasjoner"]["mva_validert"] = att("v_regnskap", timer=-1)
    assert evaluate(tjeneste, CTX, e, naa=NAA).beslutning != TILLAT


def test_attestasjon_for_feil_ressurs(tjeneste):
    e = hendelse()
    e["attestasjoner"]["mva_validert"] = att("v_regnskap", ressurs="fak-999")
    assert evaluate(tjeneste, CTX, e, naa=NAA).beslutning != TILLAT


def test_negativ_attestasjon(tjeneste):
    e = hendelse()
    e["attestasjoner"]["dublettsjekk"] = att("v_regnskap", resultat=False)
    assert evaluate(tjeneste, CTX, e, naa=NAA).beslutning != TILLAT


def test_terskelvilkaar_under_min(netthandel):
    e = {"handling": "lager.bestill_pafyll", "belop": "20000.00",
         "valuta": "NOK", "ressurs_id": "ord-1", "leverandor_id": "lev-1",
         "dataklasser": ["intern"], "dataklasser_kilde": "connector",
         "attestasjoner": {
             "leverandor_i_register": att("v_register", "ord-1"),
             "pris_innen_avtale": att("v_register", "ord-1"),
             "prognose_konfidens": att("v_prognose", "ord-1", verdi=0.75)}}
    d = evaluate(netthandel, CTX, e, teller=MinneTellerLager(), naa=NAA)
    assert d.beslutning != TILLAT
    e["attestasjoner"]["prognose_konfidens"] = att("v_prognose", "ord-1", verdi=0.85)
    d = evaluate(netthandel, CTX, e, teller=MinneTellerLager(), naa=NAA)
    assert d.beslutning == TILLAT


# ---------- Frekvens (funn A: strukturert + betrodd teller) ---------------

def purrehendelse():
    return {"handling": "purring.send", "ressurs_id": "fak-7",
            "faktura_id": "fak-7",
            "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
            "attestasjoner": {
                "forfall_passert_dager": att("v_fordring", "fak-7", verdi=20, timer=24*30),
                "ingen_aktiv_tvist": att("v_fordring", "fak-7", timer=24*30)}}


def test_frekvensregel_uten_tellerlager_stopper(tjeneste):
    d = evaluate(tjeneste, CTX, purrehendelse(), teller=None, naa=NAA)
    assert d.beslutning == STOPP  # fail-closed, aldri hopp over kontrollen


def test_frekvens_haandheves_av_betrodd_teller(tjeneste):
    lager = MinneTellerLager()
    d1 = evaluate(tjeneste, CTX, purrehendelse(), teller=lager, naa=NAA)
    assert d1.beslutning == TILLAT
    lager.registrer(d1.frekvensnokkel, NAA)
    d2 = evaluate(tjeneste, CTX, purrehendelse(), teller=lager,
                  naa=NAA + timedelta(days=3))
    assert d2.beslutning == UNNTAK and d2.unntak_kategori == "over_grense"
    d3 = evaluate(tjeneste, CTX, purrehendelse(), teller=lager,
                  naa=NAA + timedelta(days=15))  # vinduet passert
    assert d3.beslutning == TILLAT


def test_frekvensteller_fra_event_ignoreres(tjeneste):
    lager = MinneTellerLager()
    lager.registrer(("t1", "purring.send", "faktura_id", "fak-7"), NAA)
    e = purrehendelse(); e["frekvens_teller"] = 0  # angriper påstår null
    d = evaluate(tjeneste, CTX, e, teller=lager, naa=NAA + timedelta(days=3))
    assert d.beslutning == UNNTAK


# ---------- Tidssone (funn: DST/naive tidsstempler) -----------------------

def betalingshendelse(ts):
    return {"handling": "betaling.utfor", "belop": "5000.00", "valuta": "NOK",
            "ressurs_id": "fak-1", "tidspunkt": ts,
            "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
            "attestasjoner": {"faktura_godkjent": att("v_regnskap"),
                              "konto_verifisert": att("v_bank"),
                              "svindelsjekk_bestatt": att("v_svindel")}}


def test_naivt_tidsstempel_avvises(tjeneste):
    d = evaluate(tjeneste, CTX, betalingshendelse("2026-08-03T10:00:00"), naa=NAA)
    assert d.beslutning != TILLAT


def test_tidsvindu_i_policyens_sone(tjeneste):
    # 05:30 UTC = 07:30 Oslo sommertid — innenfor vinduet 07:00-17:00
    d = evaluate(tjeneste, CTX,
                 betalingshendelse("2026-08-03T05:30:00+00:00"), naa=NAA)
    assert d.beslutning == TILLAT
    # 16:00 UTC = 18:00 Oslo — utenfor
    d = evaluate(tjeneste, CTX,
                 betalingshendelse("2026-08-03T16:00:00+00:00"), naa=NAA)
    assert d.beslutning == STOPP and d.effekt == "frys"


# ---------- Logg-før-utførelse (funn: revisjonsloggfeil) ------------------

def test_sikker_beslutning_logger_for_tillat(tjeneste, tmp_path):
    logg = tmp_path / "audit.jsonl"
    d = sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)
    assert d.beslutning == TILLAT
    assert logg.exists() and logg.read_text().count("\n") == 1


def test_loggfeil_gir_stopp_aldri_utforelse(tjeneste, tmp_path):
    ulovlig = tmp_path / "finnes_ikke"
    ulovlig.write_text("")  # fil der katalog forventes -> OSError ved skriv
    d = sikker_beslutning(tjeneste, CTX, hendelse(),
                          ulovlig / "audit.jsonl", naa=NAA)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "logging_feilet"


def test_motor_exception_gir_stopp(tjeneste, tmp_path):
    # Feilformet policy som får evaluate til å feile internt
    odelagt = {"meta": "ikke et objekt", "handlinger": [{"id": None}]}
    d = sikker_beslutning(odelagt, CTX, hendelse(), tmp_path / "a.jsonl", naa=NAA)
    assert d.beslutning != TILLAT


def test_loggpost_strukturert_begrunnelse(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(), naa=NAA)
    post = lag_loggpost(d, hendelse(), tjeneste)
    assert all("kode" in g for g in post["begrunnelse"])
    assert post["input_hash"] and post["policy_id"]
