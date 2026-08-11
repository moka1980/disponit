"""PR-013 CP4: semantikkmanifest + versjonsbinding + oppstartsverifikasjon.

En CI-port beskytter ikke produksjon om verten kjører annen tzdata (V8). Her
bindes motorsemantikk til en checksum (port 3), klassifikatorversjonen utledes
av den (v3 §4), DENY_ALL_V1 er effektiv deny (V9), og oppstartssjekken stopper
ved semantikk-/miljøavvik (port 13).
"""
import hashlib

import pytest

from policy_validator import klassifikator as kl
from policy_validator import semantikk
from policy_validator.engine import EvaluationContext, evaluate, TILLAT
from policy_validator.semantikk import SemantikkAvvik


def test_semantikk_checksum_pinnet_mot_kilden():
    """Port 3 / V8: den pinnede motorversjonen MÅ være lik den beregnede
    kildechecksummen. Endres en manifestfil uten å oppdatere pinnen → rødt."""
    assert semantikk.kildechecksum() == semantikk.MOTOR_SEMANTIKKVERSJON, (
        "en semantikkfil er endret uten at MOTOR_SEMANTIKKVERSJON ble bumpet "
        "(oppdater pinnen i semantikk.py til den nye kildechecksummen)")


def test_manifest_filer_finnes():
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    for rel in semantikk.SEMANTIKK_MANIFEST:
        assert (rot / rel).is_file(), f"manifestfil mangler: {rel}"


def test_klassifikatorversjon_utledes_av_motorsemantikk():
    """v3 §4: klassifikatorversjonen er en funksjon av motorsemantikkversjonen —
    en motorendring bumper den ALLTID. Bevises ved å endre motorversjonen og se
    at den utledede klassifikatorversjonen endres."""
    egen = hashlib.sha256(__import__("pathlib").Path(
        kl.__file__).read_bytes()).hexdigest()
    v_naa = "kv-" + hashlib.sha256(
        (semantikk.MOTOR_SEMANTIKKVERSJON + "|" + egen).encode()).hexdigest()[:16]
    v_annen_motor = "kv-" + hashlib.sha256(
        ("EN-ANNEN-MOTORVERSJON|" + egen).encode()).hexdigest()[:16]
    assert kl.KLASSIFIKATORVERSJON == v_naa
    assert v_annen_motor != v_naa, "motorendring bumper ikke klassifikatorversjon"


def test_deny_all_v1_er_effektiv_deny():
    """V9: DENY_ALL_V1 gir aldri TILLAT — ingen handling kan auto-utføres for en
    tenant uten aktiv policy (ukjent handling → ikke-TILLAT)."""
    ctx = EvaluationContext(tenant_id="t", aktor_rolle="agent",
                            autentisert=True, kilde="api_token")
    for h in ("faktura.bokfor", "utbetaling.stor", "hva.som.helst"):
        d = evaluate(semantikk.DENY_ALL_V1, ctx, {"handling": h})
        assert d.beslutning != TILLAT, f"DENY_ALL tillot {h}"
    # hash er stabil + bundet
    assert semantikk.DENY_ALL_HASH == semantikk.deny_all_hash()


def test_miljosignatur_endres_med_tzdata(monkeypatch):
    a = semantikk.miljosignatur()
    monkeypatch.setattr(semantikk, "tzdata_versjon", lambda: "tzdata-pip:9999z")
    b = semantikk.miljosignatur()
    assert a != b, "miljøsignaturen fanget ikke en tzdata-endring"


def test_oppstart_ok_uten_miljo_env(monkeypatch):
    monkeypatch.delenv("DISPONIT_SEMANTIKK_MILJO", raising=False)
    semantikk.verifiser_oppstartsmiljo()          # skal ikke kaste


def test_oppstart_stopper_ved_miljoavvik(monkeypatch):
    """Port 13: verten har annen tzdata enn releasen ble bygget med → STOPP."""
    monkeypatch.setenv("DISPONIT_SEMANTIKK_MILJO", "et-annet-miljo-enn-vertens")
    with pytest.raises(SemantikkAvvik):
        semantikk.verifiser_oppstartsmiljo()
    # riktig miljø → ingen feil
    monkeypatch.setenv("DISPONIT_SEMANTIKK_MILJO", semantikk.miljosignatur())
    semantikk.verifiser_oppstartsmiljo()


def test_oppstart_stopper_ved_kildeendring(monkeypatch):
    """En semantikkfil endret uten versjonsbump (pinnen stemmer ikke) → STOPP
    ved oppstart, ikke bare i CI."""
    monkeypatch.delenv("DISPONIT_SEMANTIKK_MILJO", raising=False)
    monkeypatch.setattr(semantikk, "MOTOR_SEMANTIKKVERSJON", "feil-pin")
    with pytest.raises(SemantikkAvvik):
        semantikk.verifiser_oppstartsmiljo()
