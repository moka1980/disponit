"""Søsterfunksjonen i akseptmaskineriet (054, m02-aksept-klarsignalet
6d1cf8ecb850e457): aksept for PLATTFORMMODULER uten deploymentrad —
innholdsadressert identitet, «utenfor grensen» som førsteklasses
tilstand med påkrevd begrunnelse, delte målinger referert VED HASH og
målt mot evidensattesten, attestant ≠ akseptør på session_user,
SP-2-replay, append-only.

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
#: Den delte målingen testene refererer: ÉN sti og ÉN sha, attestert av
#: verifikatoren for hvert artefaktpunkt — delingen er nettopp at flere
#: punkter hviler på de samme bytene.
ARTEFAKT_STI = "deploy/staging/artefakter/x.json"
ARTEFAKT_SHA = "ab" * 32
#: Identitetspunktet: manifestet selv, lest og hashet av verifikatoren.
MANIFEST_STI = "platform/modules/m02_revisjonslogg/manifest.yaml"
MANIFEST_SHA = "cd" * 32


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


def _artefaktkrav(m):
    """Registerets grønne fasit for de ORDINÆRE artefaktpunktene —
    {punkt: krav}. Sentinelpunktene (skoping, identitet) står utenfor:
    de har ingen literal fasit i registeret."""
    return {p: krav for p, krav in m.execute(
        "SELECT punkt, maalt_krav FROM akseptkrav_punkt"
        "  WHERE krav_id=%s AND kilde_type='artefakt'"
        "    AND maalt_krav NOT LIKE '<%%>'", (GRENSE,)).fetchall()}


def _identitetspunkt(m):
    """Punktet med `<manifestets sha256>`-sentinelen, eller None."""
    rad = m.execute(
        "SELECT punkt FROM akseptkrav_punkt"
        "  WHERE krav_id=%s AND maalt_krav='<manifestets sha256>'",
        (GRENSE,)).fetchone()
    return rad and rad[0]


def _manifestattest(m, sha=MANIFEST_SHA, sti=MANIFEST_STI, verdi=None):
    """Referatet om MANIFESTET: verifikatoren leste stien, hashet bytene
    og skrev hva de bar for identitetspunktet — digesten selv."""
    punkt = _identitetspunkt(m)
    if punkt is None:
        return
    _evidensattest(m, punkter={punkt: verdi if verdi is not None else sha},
                   sti=sti, sha=sha)


def _evidensattest(m, punkter=None, sti=ARTEFAKT_STI, sha=ARTEFAKT_SHA):
    """Referatet fra veien som LESTE målingen (Codex P1, runde 1):
    `evidensfil_attest`-rader skrevet av VERIFIKATOREN — en annen
    autentisert identitet enn akseptøren."""
    m.execute("RESET ROLE")
    v = _verifikator()
    try:
        v.execute("SELECT attester_evidensfil(%s,%s,%s,%s::jsonb,'test')",
                  (GRENSE, sti, sha,
                   json.dumps(punkter if punkter is not None
                              else _artefaktkrav(m))))
        v.commit()
    finally:
        v.close()


def _punkter(m, ci_run, ci_commit=CI_SHA, manifest_sha=MANIFEST_SHA):
    """Punktsettet slik registeret krever det — grønne verdier, riktige
    kildeformer (artefakt VED HASH — også de delte målingene, som eies av
    et annet punkts artefakt — ci_kjoring på akseptens egen kjøring),
    begrunnelse for de skopede."""
    ut = {}
    for punkt, kt, grense, krav in m.execute(
            "SELECT punkt, kilde_type, grenseverdi, maalt_krav"
            "  FROM akseptkrav_punkt WHERE krav_id=%s", (GRENSE,)):
        if krav == "<utenfor grensen>":
            ut[punkt] = {"status": "utenfor_grensen",
                         "begrunnelse": grense}
        elif krav == "<manifestets sha256>":
            # Identitetspunktet: den grønne verdien ER akseptens digest,
            # og referansen navngir manifestets egne bytes.
            ut[punkt] = {"status": "maalt", "grenseverdi": grense,
                         "maalt_verdi": manifest_sha, "kilde_type": kt,
                         "kilde_ref": f"{MANIFEST_STI}"
                                      f"@sha256:{manifest_sha}"}
        elif kt == "artefakt":
            ut[punkt] = {"status": "maalt", "grenseverdi": grense,
                         "maalt_verdi": krav, "kilde_type": kt,
                         "kilde_ref": f"{ARTEFAKT_STI}"
                                      f"@sha256:{ARTEFAKT_SHA}"}
        else:
            ut[punkt] = {"status": "maalt", "grenseverdi": grense,
                         "maalt_verdi": krav, "kilde_type": kt,
                         "kilde_ref": f"run {ci_run} @ {ci_commit}"}
    return ut


def _aksepter(m, *, modul=None, commit=None, sha=None, punkter=None,
              ci_run=None, ci_commit=None, nokkel=None, attest=True,
              evidens=True):
    modul = modul or "m02_test_" + secrets.token_hex(3)
    commit = commit or secrets.token_hex(20)
    ci_run = ci_run or "run-" + secrets.token_hex(4)
    # CI-commiten ER identiteten (funksjonens egen regel) — tester som
    # skal måle nettopp den porten sender eksplisitt avvik.
    if ci_commit is None:
        ci_commit = commit
    sha = sha or MANIFEST_SHA
    m.execute("RESET ROLE")
    if punkter is None:
        punkter = _punkter(m, ci_run, ci_commit, sha)
    if attest:
        _ci_attest(m, ci_run, ci_commit)
    if evidens:
        _evidensattest(m)
        # …og referatet om manifestet, når digesten i det hele tatt har
        # en form å lese (formporten måles av egne tester).
        if len(sha) == 64 and all(t in "0123456789abcdef" for t in sha):
            _manifestattest(m, sha=sha)
    m.execute("SET ROLE disponit_modules_admin")
    m.execute(
        "SELECT aksepter_plattformmodul(%s,%s,%s,%s,%s,%s,%s::jsonb,"
        "%s,'test')",
        (modul, commit, sha, GRENSE, ci_run, ci_commit,
         json.dumps(punkter), nokkel or "pn-" + secrets.token_hex(6)))
    m.execute("RESET ROLE")
    return modul, commit, ci_run


@pg
def test_grensen_star_i_registeret_uten_onskepunkter(migrator):
    """Port 13 (ønskepunkt-vernet): hvert punkt i grensen måler en
    mekanisme m02 HAR — og skopingen er registerets, eksplisitt: nøyaktig
    de to punktene for mekanismer m02 ikke har står som `<utenfor
    grensen>`-sentinel, og ingen punkter nevner egress/browser/proxy.
    De åtte klarsignalpunktene + identitetspunktet (Codex P1, runde 1),
    som ikke er et ønskepunkt men SP-12-invarianten gjort målbar."""
    migrator.execute("RESET ROLE")
    rader = {p: (kt, krav) for p, kt, krav in migrator.execute(
        "SELECT punkt, kilde_type, maalt_krav FROM akseptkrav_punkt"
        "  WHERE krav_id=%s", (GRENSE,)).fetchall()}
    migrator.rollback()
    assert len(rader) == 9, sorted(rader)
    assert {p for p, (_, krav) in rader.items()
            if krav == "<utenfor grensen>"} == UTENFOR
    assert {p for p, (_, krav) in rader.items()
            if krav == "<manifestets sha256>"} == {"manifest_identitet"}
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
    assert (n_h, n_p, n_u) == (1, 9, 2)
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
    # …og materialiteten er HELE observasjonen: også `grenseverdi` og
    # `kilde_type` (Codex P2, runde 1). Replayet returnerer før den
    # vanlige registerkontrollen, så et retry som bytter nettopp de to
    # ville ellers fått «vellykket» på noe basen aldri lagret.
    for felt, verdi in (("grenseverdi", "min egen grense"),
                        ("kilde_type", "ci_kjoring")):
        p = _punkter(migrator, ci_run)
        p["ytelse_bestatt"][felt] = verdi
        with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
            _aksepter(migrator, modul=modul, commit=commit, ci_run=ci_run,
                      nokkel=nokkel, punkter=p, attest=False)
        assert "andre punktobservasjoner" in str(ei.value), felt
        migrator.rollback()
    # …og sammenligningen går BEGGE VEIER (Codex P2, runde 2): et retry
    # med et EKSTRA toppnivåpunkt finner hver lagrede rad uendret, og
    # ville ellers fått «vellykket» på en påstand den vanlige veien
    # avviser — og som aldri ble lagret.
    p = _punkter(migrator, ci_run)
    p["banan_punkt"] = {"status": "maalt"}
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, modul=modul, commit=commit, ci_run=ci_run,
                  nokkel=nokkel, punkter=p, attest=False)
    assert "aldri ble lagret" in str(ei.value)
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
def test_hashen_maales_mot_referatet_ikke_mot_formen(migrator):
    """Codex P1, runde 1: en velformet hash er ingen måling. Punktet
    måles mot `evidensfil_attest` — referatet fra veien som faktisk
    hashet artefaktet — på fire ledd: at raden FINNES, at stien er
    attestens (likhet, ikke hale), at det er MÅLINGENS tall som oppfyller
    kravet, og at referatet er skrevet av en annen innlogging enn
    aksepten."""
    migrator.execute("RESET ROLE")
    # 1. oppdiktede bytes med riktig hale — ingen attest, ingen aksept.
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["ytelse_bestatt"]["kilde_ref"] = f"{ARTEFAKT_STI}@sha256:{'ee' * 32}"
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "ingen attest sier at de bytene er lest" in str(ei.value)
    migrator.rollback()
    # 2. attesterte bytes, men en annen sti: en observasjon navngir DEN
    #    målingen som ble lest, ikke en sti med riktig hale.
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["ytelse_bestatt"]["kilde_ref"] = f"annen/sti.json@sha256:{ARTEFAKT_SHA}"
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "riktig hale" in str(ei.value)
    migrator.rollback()
    # 3. referatet bar et RØDT tall — kallerens grønne gjentakelse av
    #    registerets fasit hjelper ikke.
    roed = "dd" * 32
    _evidensattest(migrator, punkter={"ytelse_bestatt": "5999/6000"},
                   sha=roed)
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["ytelse_bestatt"]["kilde_ref"] = f"{ARTEFAKT_STI}@sha256:{roed}"
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "regner mot det målingen SA" in str(ei.value)
    migrator.rollback()
    # 4. …og fire øyne, som på CI-attesten: et referat skrevet av
    #    akseptørens egen innlogging gjennom eierrollen teller ikke.
    selv = "cc" * 32
    krav = _artefaktkrav(migrator)["ytelse_bestatt"]
    migrator.execute("SET ROLE disponit_modul_eier")
    migrator.execute(
        "SELECT attester_evidensfil(%s,%s,%s,%s::jsonb,'test')",
        (GRENSE, ARTEFAKT_STI, selv, json.dumps({"ytelse_bestatt": krav})))
    migrator.execute("RESET ROLE")
    migrator.commit()
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p["ytelse_bestatt"]["kilde_ref"] = f"{ARTEFAKT_STI}@sha256:{selv}"
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "to autentiserte identiteter" in str(ei.value)
    migrator.rollback()


@pg
def test_manifestdigesten_er_malt_ikke_pastatt(migrator):
    """Codex P1, runde 1: `manifest_sha` var bare formkontrollert, så en
    kaller med gyldig CI-attest kunne feste hvilken som helst velformet
    64-hex til en immutabel aksept. Identitetspunktet binder digesten til
    bytes verifikatoren har lest: uten referat, med referanse til ANDRE
    bytes, med en påstått verdi eller med et referat som bar noe annet —
    ingen aksept. Og står den, bærer raden digesten."""
    migrator.execute("RESET ROLE")
    punkt = _identitetspunkt(migrator)
    assert punkt is not None
    # 1. en digest ingen har lest: attestene for de øvrige punktene
    #    finnes, manifestets gjør det ikke.
    fri = "ba" * 32
    _evidensattest(migrator)
    ci_run = "run-" + secrets.token_hex(4)
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, sha=fri, evidens=False)
    assert "ingen attest sier at de bytene er lest" in str(ei.value)
    migrator.rollback()
    # 2. attesterte bytes — men et ANNET artefakt enn manifestet.
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p[punkt]["kilde_ref"] = f"{ARTEFAKT_STI}@sha256:{ARTEFAKT_SHA}"
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "manifestets egne bytes" in str(ei.value)
    migrator.rollback()
    # 3. en påstått verdi som ikke er akseptens digest.
    ci_run = "run-" + secrets.token_hex(4)
    p = _punkter(migrator, ci_run)
    p[punkt]["maalt_verdi"] = "ff" * 32
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, punkter=p)
    assert "grønn observasjon" in str(ei.value)
    migrator.rollback()
    # 4. referatet om manifestet bar noe annet enn digesten.
    annen = "bc" * 32
    _evidensattest(migrator)
    _manifestattest(migrator, sha=annen, verdi="et helt annet manifest")
    ci_run = "run-" + secrets.token_hex(4)
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, ci_run=ci_run, sha=annen, evidens=False)
    assert "regner mot det målingen SA" in str(ei.value)
    migrator.rollback()
    # …og den grønne veien: raden bærer digesten som målt verdi.
    modul, commit, _ = _aksepter(migrator)
    migrator.commit()
    migrator.execute("RESET ROLE")
    sha, verdi, ref = migrator.execute(
        "SELECT a.manifest_sha256, p.maalt_verdi, p.kilde_ref"
        "  FROM plattformmodulaksept a JOIN plattformmodulaksept_punkt p"
        "    ON (p.modul_id, p.manifest_commit, p.grense_id)"
        "     = (a.modul_id, a.manifest_commit, a.grense_id)"
        " WHERE a.modul_id=%s AND p.punkt=%s",
        (modul, punkt)).fetchone()
    migrator.rollback()
    assert (sha, verdi) == (MANIFEST_SHA, MANIFEST_SHA)
    assert ref == f"{MANIFEST_STI}@sha256:{MANIFEST_SHA}"


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
    # …og punktet må STÅ I GRENSEN, også på denne veien (Codex P2,
    # runde 2): et komplett-utseende smugpunkt som aldri var en del av
    # det aksepterte settet, felles av FK-en mot kravpunktregisteret —
    # ikke bare av funksjonen kalleren her går utenom.
    migrator.execute("SET ROLE disponit_modul_eier")
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as ei:
        migrator.execute(
            "INSERT INTO plattformmodulaksept_punkt (modul_id,"
            " manifest_commit, grense_id, punkt, status, grenseverdi,"
            " maalt_verdi, kilde_type, kilde_ref) VALUES"
            " (%s,%s,%s,'smugpunkt','maalt','x','x','artefakt',%s)",
            (modul, commit, GRENSE, "x@sha256:" + "ab" * 32))
    assert "akseptkrav_punkt" in str(ei.value)
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


def test_det_delte_kildedomenet_er_urort():
    """Codex P2, runde 1 (statisk): 054 utvider ikke `akseptkrav_punkt`s
    FELLES kildedomene. En type deployment-veien ikke kan måle, ville
    vært en registrerbar tilstand som aldri kan gi en aksept — delingen
    bæres av hashen og evidensattesten, ikke av en egen kildetype."""
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    sql = (rot / "platform/core/db/migrations"
           / "054_plattformmodulaksept.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE akseptkrav_punkt" not in sql
    assert "delt_maaling'" not in sql
