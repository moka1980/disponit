"""Søsterfunksjonen i akseptmaskineriet (054, m02-aksept-klarsignalet
6d1cf8ecb850e457): aksept for PLATTFORMMODULER uten deploymentrad —
innholdsadressert identitet, «utenfor grensen» som førsteklasses
tilstand med påkrevd begrunnelse, delte målinger referert VED HASH,
attestant ≠ akseptør på session_user, SP-2-replay, append-only.

Portene her er klarsignalets §5 (1–9 + 13). Flipp-portene 14/15 bor i
registry-/manifesttestene og m56-arcen.
"""
from __future__ import annotations

import json
import os
import secrets

import psycopg
import pytest

from .test_api import migrator, miljo  # noqa: F401 — delte fixturer
from .test_modulaksept import _rene_attester  # noqa: F401 — attestrydding

DSN = os.environ.get("DISPONIT_TEST_DSN")
VERIFIKATOR_DSN = os.environ.get("DISPONIT_TEST_VERIFIKATOR_DSN")
pg = pytest.mark.skipif(
    not (DSN and VERIFIKATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_VERIFIKATOR_DSN ikke satt")

GRENSE = "m02-aksept-v1"
CI_SHA = "f" * 40
#: De åtte punktene slik registeret (054) definerer dem — testene leser
#: fasiten fra basen, dette settet brukes bare til statiske påstander.
UTENFOR = {"moduldrill_boot", "flate_axe_tastatur"}


def _verifikator():
    from db.pg import koble
    return koble(VERIFIKATOR_DSN)


def _ci_attest(m, ci_run, ci_commit=CI_SHA):
    arbeidsflyt = m.execute(
        "SELECT arbeidsflyt FROM akseptkrav_ci WHERE krav_id=%s",
        (GRENSE,)).fetchone()[0]
    v = _verifikator()
    try:
        v.execute("SELECT attester_ci_kjoring(%s,%s,'push','main',"
                  "'success',%s,'test')", (ci_run, arbeidsflyt, ci_commit))
        v.commit()
    finally:
        v.close()


def _punkter(m, ci_run, ci_commit=CI_SHA):
    """Punktsettet slik registeret krever det — grønne verdier, riktige
    kildeformer (delt måling/artefakt VED HASH, ci_kjoring på akseptens
    egen kjøring), begrunnelse for de skopede."""
    ut = {}
    for punkt, kt, grense, krav in m.execute(
            "SELECT punkt, kilde_type, grenseverdi, maalt_krav"
            "  FROM akseptkrav_punkt WHERE krav_id=%s", (GRENSE,)):
        if krav == "<utenfor grensen>":
            ut[punkt] = {"status": "utenfor_grensen",
                         "begrunnelse": grense}
        elif kt in ("delt_maaling", "artefakt"):
            ut[punkt] = {"status": "maalt", "grenseverdi": grense,
                         "maalt_verdi": krav, "kilde_type": kt,
                         "kilde_ref": f"deploy/staging/artefakter/x.json"
                                      f"@sha256:{'ab' * 32}"}
        else:
            ut[punkt] = {"status": "maalt", "grenseverdi": grense,
                         "maalt_verdi": krav, "kilde_type": kt,
                         "kilde_ref": f"run {ci_run} @ {ci_commit}"}
    return ut


def _aksepter(m, *, modul=None, commit=None, sha=None, punkter=None,
              ci_run=None, ci_commit=None, nokkel=None, attest=True):
    modul = modul or "m02_test_" + secrets.token_hex(3)
    commit = commit or secrets.token_hex(20)
    ci_run = ci_run or "run-" + secrets.token_hex(4)
    # CI-commiten ER identiteten (funksjonens egen regel) — tester som
    # skal måle nettopp den porten sender eksplisitt avvik.
    if ci_commit is None:
        ci_commit = commit
    m.execute("RESET ROLE")
    if punkter is None:
        punkter = _punkter(m, ci_run, ci_commit)
    if attest:
        _ci_attest(m, ci_run, ci_commit)
    m.execute("SET ROLE disponit_modules_admin")
    m.execute(
        "SELECT aksepter_plattformmodul(%s,%s,%s,%s,%s,%s,%s::jsonb,"
        "%s,'test')",
        (modul, commit, sha or "cd" * 32, GRENSE, ci_run, ci_commit,
         json.dumps(punkter), nokkel or "pn-" + secrets.token_hex(6)))
    m.execute("RESET ROLE")
    return modul, commit, ci_run


@pg
def test_grensen_star_i_registeret_uten_onskepunkter(migrator):
    """Port 13 (ønskepunkt-vernet): hvert punkt i grensen måler en
    mekanisme m02 HAR — og skopingen er registerets, eksplisitt: nøyaktig
    de to punktene for mekanismer m02 ikke har står som `<utenfor
    grensen>`-sentinel, og ingen punkter nevner egress/browser/proxy."""
    migrator.execute("RESET ROLE")
    rader = {p: (kt, krav) for p, kt, krav in migrator.execute(
        "SELECT punkt, kilde_type, maalt_krav FROM akseptkrav_punkt"
        "  WHERE krav_id=%s", (GRENSE,)).fetchall()}
    migrator.rollback()
    assert len(rader) == 8, sorted(rader)
    assert {p for p, (_, krav) in rader.items()
            if krav == "<utenfor grensen>"} == UTENFOR
    for navn in rader:
        assert not any(ord_ in navn for ord_ in
                       ("egress", "browser", "proxy")), navn
    # CI-kontrakten finnes (en aksept uten CI-krav finnes ikke).
    assert migrator.execute(
        "SELECT count(*) FROM akseptkrav_ci WHERE krav_id=%s",
        (GRENSE,)).fetchone()[0] == 1
    migrator.rollback()


@pg
def test_aksepten_ende_til_ende_med_replay(migrator):
    """Positiv kontroll + port 7: hendelsen skrives med komplett
    punktsett (målt OG skopet som rader), registerhendelsen bærer
    identiteten, og et identisk replay er en no-op — mens samme nøkkel
    med en annen observasjon avvises."""
    nokkel = "pn-" + secrets.token_hex(6)
    modul, commit, ci_run = _aksepter(migrator, nokkel=nokkel)
    migrator.commit()
    migrator.execute("RESET ROLE")
    n_h, n_p, n_u = migrator.execute(
        "SELECT (SELECT count(*) FROM plattformmodulaksept"
        "         WHERE modul_id=%s),"
        "       (SELECT count(*) FROM plattformmodulaksept_punkt"
        "         WHERE modul_id=%s),"
        "       (SELECT count(*) FROM plattformmodulaksept_punkt"
        "         WHERE modul_id=%s AND status='utenfor_grensen'"
        "           AND begrunnelse IS NOT NULL)",
        (modul, modul, modul)).fetchone()
    hendelse = migrator.execute(
        "SELECT detalj->>'manifest_commit' FROM modulregister_hendelse"
        " WHERE modul_id=%s AND hendelse='plattformmodulaksept'",
        (modul,)).fetchone()[0]
    migrator.rollback()
    assert (n_h, n_p, n_u) == (1, 8, 2)
    assert hendelse == commit
    # identisk replay -> no-op (attesten finnes alt)
    _aksepter(migrator, modul=modul, commit=commit, ci_run=ci_run,
              nokkel=nokkel, attest=False)
    migrator.commit()
    migrator.execute("RESET ROLE")
    assert migrator.execute(
        "SELECT count(*) FROM plattformmodulaksept WHERE modul_id=%s",
        (modul,)).fetchone()[0] == 1
    migrator.rollback()
    # samme nøkkel, annen observasjon -> avvist
    p = _punkter(migrator, ci_run)
    p["ytelse_bestatt"]["kilde_ref"] = (
        "deploy/staging/artefakter/y.json@sha256:" + "ee" * 32)
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, modul=modul, commit=commit, ci_run=ci_run,
                  nokkel=nokkel, punkter=p, attest=False)
    assert "andre punktobservasjoner" in str(ei.value)
    migrator.rollback()


@pg
def test_punktsettet_er_komplett_eller_ingen_hendelse(migrator):
    """Port 1 + 2: manglende punkt, ukjent punkt og et grensepunkt uten
    måling blokkerer — UMAALTE-regelen i søsterform."""
    migrator.execute("RESET ROLE")
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    del p["revisjonslogg_korrekt"]
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "ufullstendig" in str(ei.value)
    migrator.rollback()
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["banan_punkt"] = {"status": "maalt"}
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "står ikke i grensen" in str(ei.value)
    migrator.rollback()
    # et punkt i grensen kan ikke skopes bort av KALLEREN…
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["ytelse_bestatt"] = {"status": "utenfor_grensen",
                           "begrunnelse": "orker ikke"}
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "må være MÅLT" in str(ei.value)
    migrator.rollback()
    # …og et registerskopet punkt kan ikke pyntes til målt.
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["moduldrill_boot"] = {"status": "maalt", "grenseverdi": "x",
                            "maalt_verdi": "x", "kilde_type": "artefakt",
                            "kilde_ref": "x@sha256:" + "ab" * 32}
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "skopet utenfor grensen" in str(ei.value)
    migrator.rollback()


@pg
def test_maalinger_regnes_mot_registeret_og_kilder_peker(migrator):
    """Port 9 + grensedisiplinen: verdier og grenser er registerets;
    en delt måling refereres VED HASH — en beskrivelse avvises — og
    ci_kjoring må navngi akseptens egen kjøring."""
    migrator.execute("RESET ROLE")
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["ytelse_bestatt"]["maalt_verdi"] = "5999/6000"
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "grønn observasjon" in str(ei.value)
    migrator.rollback()
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["revisjonslogg_korrekt"]["kilde_ref"] = \
        "samme type måling som r21-runden"
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "refererer ikke ved hash" in str(ei.value)
    migrator.rollback()
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["ytelse_bestatt"]["grenseverdi"] = "min egen grense"
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "registerets" in str(ei.value)
    migrator.rollback()


@pg
def test_identiteten_er_innholdet_og_ci_er_attestert(migrator):
    """SP-11/SP-12 + attestporten: ugyldige former avvises, CI-commiten
    MÅ være identitetens commit, og uten referat fra veien som spurte
    GitHub finnes ingen aksept."""
    migrator.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, commit="ikke-hex", attest=False)
    assert "identiteten er innholdet" in str(ei.value)
    migrator.rollback()
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, sha="kort", attest=False)
    assert "identiteten er innholdet" in str(ei.value)
    migrator.rollback()
    ci_run = "run-" + secrets.token_hex(4)
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, commit="ab" * 20, ci_commit=CI_SHA,
                  ci_run=ci_run, attest=False)
    assert "akseptcommiten" in str(ei.value)
    migrator.rollback()
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, attest=False)
    assert "ingen attest" in str(ei.value)
    migrator.rollback()


@pg
def test_attestant_er_ikke_akseptor(migrator):
    """Port 6 (052-formen, målt på session_user): et referat skrevet av
    akseptørens egen innlogging gjennom eierrollen avvises — to
    autentiserte identiteter, eller ingen aksept."""
    migrator.execute("RESET ROLE")
    arbeidsflyt = migrator.execute(
        "SELECT arbeidsflyt FROM akseptkrav_ci WHERE krav_id=%s",
        (GRENSE,)).fetchone()[0]
    commit = secrets.token_hex(20)
    migrator.execute("SET ROLE disponit_modul_eier")
    migrator.execute(
        "SELECT attester_ci_kjoring(%s,%s,'push','main','success',%s,"
        "'test')", ("run-selv-pm", arbeidsflyt, commit))
    migrator.execute("RESET ROLE")
    migrator.commit()
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, commit=commit, ci_run="run-selv-pm",
                  attest=False)
    assert "samme innlogging" in str(ei.value)
    migrator.rollback()


@pg
def test_hendelsen_og_punktene_er_immutable(migrator):
    """Port 5 + 8: radene tåler ingen UPDATE/DELETE, ordinære roller
    når verken tabellene eller funksjonen, og en direkte INSERT som
    bryter punkt_komplett (utenfor uten begrunnelse) avvises av
    CHECK-en selv for eierrollen (port 3/4)."""
    modul, commit, _ = _aksepter(migrator)
    migrator.commit()
    migrator.execute("RESET ROLE")
    for sql in (
            "UPDATE plattformmodulaksept SET grense_id='x'"
            " WHERE modul_id=%s",
            "DELETE FROM plattformmodulaksept WHERE modul_id=%s",
            "UPDATE plattformmodulaksept_punkt SET maalt_verdi='9'"
            " WHERE modul_id=%s",
            "DELETE FROM plattformmodulaksept_punkt WHERE modul_id=%s"):
        with pytest.raises(psycopg.errors.RaiseException):
            migrator.execute(sql, (modul,))
        migrator.rollback()
    # port 3/4: CHECK-en holder også mot eierrollens egen INSERT.
    migrator.execute("SET ROLE disponit_modul_eier")
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO plattformmodulaksept_punkt (modul_id,"
            " manifest_commit, grense_id, punkt, status) VALUES"
            " (%s,%s,%s,'smugpunkt','utenfor_grensen')",
            (modul, commit, GRENSE))
    migrator.rollback()
    # port 8: verifikator (ordinær, smal rolle) når ingenting.
    v = _verifikator()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            v.execute("INSERT INTO plattformmodulaksept (modul_id,"
                      " manifest_commit, manifest_sha256, grense_id,"
                      " ci_run, ci_commit, nokkel, aktor) VALUES"
                      " ('x',%s,%s,'g','r','c','n','a')",
                      ("ab" * 20, "cd" * 32))
        v.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            v.execute("SELECT aksepter_plattformmodul('x',%s,%s,'g','r',"
                      "%s,'{}'::jsonb,'n','a')",
                      ("ab" * 20, "cd" * 32, "ab" * 20))
        v.rollback()
    finally:
        v.close()


def test_ingen_syntetiske_registerrader_i_migrasjonen():
    """Sikkerhetsinvarianten `register.syntetisk_rad_opprettet = 0`
    (statisk): 054 rører ALDRI modulhode/modulrelease/moduldeployment —
    registeret beskriver virkeligheten, det gjøres ikke kompatibelt med
    en funksjonssignatur."""
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    sti = (rot / "platform/core/db/migrations"
           / "054_plattformmodulaksept.sql")
    sql = sti.read_text(encoding="utf-8")
    for tabell in ("modulhode", "modulrelease", "moduldeployment",
                   "modulkontrakt"):
        assert f"INSERT INTO {tabell}" not in sql \
            and f"INSERT INTO public.{tabell}" not in sql, tabell
