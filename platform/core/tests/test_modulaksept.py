"""049 — modulaksept: Codex-portene fra m56-akseptflipp-klarsignalet.

Aksept er en bevisbåren hendelse: drillraden (FK med utfallene i den
refererte nøkkelen — E1f), det promoterte E2E-artefaktet fra NØYAKTIG
den aksepterte releasen (delt release_id i FK-en — E1e), og én
observasjon per grensepunkt, komplett eller ingenting.

DOKUMENTERT AVVIK (migrasjonshodet): livsløpet er enveis, så drillen
konsumerer den drillede releasen og aksepten binder AKSEPTKANDIDATEN —
raden som faktisk kjører. Digestlikhets-porten i `registrer_moduldrill`
holder A1: aksepterte bytes er drillede bytes. Testene her kjører med
IDENTISK digest på alle releasene med vilje — porten skal bevise at
IDENTITETEN bærer, ikke bytene (alle m56-releaser i prod deler digest).

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import hashlib
import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, dekker, migrator, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]
M049 = ROT / "platform/core/db/migrations/049_modulaksept.sql"
KRAV = "wcag-kontroll-v1"
SHA0 = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


def _rt():
    from db.pg import koble
    return koble(DSN)


def _kjede(m, *, promoter_paa_drillet=False, staged_paa_kandidat=False):
    """Full modulkjede for én test: to deployments (drenert + claiming),
    promotert artefakt på kandidaten. -> dict med identitetene."""
    mid = "m_aksept_" + secrets.token_hex(3)
    ten = "t-aksept-" + secrets.token_hex(3)
    m.execute("SELECT set_config('disponit.tenant', %s, false),"
              " set_config('disponit.aktor', 'test', false)", (ten,))
    m.execute("INSERT INTO modulhode (modul_id, status) VALUES (%s,'aktiv')",
              (mid,))
    m.execute("INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
              " kontrakt_hash, payload_schema_hash, kvittering_schema_hash,"
              " sideeffektklasse, reversibilitet) VALUES"
              " (%s,1,'kh','ph','qh','ekstern_lesing','direkte')", (mid,))
    for rel in ("r-drillet", "r-rullback", "r-kandidat"):
        m.execute("INSERT INTO modulrelease (modul_id, release_id,"
                  " kontraktversjon, kontrakt_hash, manifest_hash,"
                  " artifact_digest) VALUES (%s,%s,1,'kh','mh','digest-x')",
                  (mid, rel))
    for rel, livslop in (("r-drillet", "draining"),
                         ("r-rullback", "draining"),
                         ("r-kandidat", "claiming")):
        m.execute("INSERT INTO moduldeployment (modul_id, release_id,"
                  " kontraktversjon, kontrakt_hash, miljo, livslop) VALUES"
                  " (%s,%s,1,'kh','staging',%s)", (mid, rel, livslop))
    m.execute("INSERT INTO artefaktskjema (skjema_hash, kanonisk) VALUES"
              " (%s,'{}') ON CONFLICT DO NOTHING", (SHA0,))
    at = f"aksept.rapport.{mid}"
    m.execute("INSERT INTO artefakttype_register (artefakttype, eiermodul,"
              " kontraktversjon, kontrakt_hash, skjema_hash) VALUES"
              " (%s,%s,1,'kh',%s)", (at, mid, SHA0))
    m.execute("INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek)"
              " VALUES (%s,'k1','\\x00'::bytea) ON CONFLICT DO NOTHING",
              (ten,))

    def artefakt(rel, tilstand):
        blid = m.execute(
            "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
            " beslutning, begrunnelse, idempotency_key, kilde) VALUES"
            " (%s,'h','p','TILLAT','[]'::jsonb,%s,'arbeidskapabilitet')"
            " RETURNING id", (ten, secrets.token_hex(8))).fetchone()[0]
        oid = m.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            " handling, eiermodul, status, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, koblingsstatus,"
            " beslutning_loggpost_id) VALUES ('beslutning',%s,"
            "'kontroll.wcag.nettsted','kontroll.wcag.nettsted',%s,'utfort',"
            "%s,'k1',%s, now()+interval '1 hour', now()+interval '2 hours',"
            "'KOBLET',%s) RETURNING id",
            (ten, mid, b"\x00" * 24, b"\x00" * 12, blid)).fetchone()[0]
        return m.execute(
            "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype,"
            " modul_id, release_id, kontraktversjon, kontrakt_hash,"
            " module_epoch, tilstand, storrelse_bytes, klartekst_sha256,"
            " ciphertext, nonce, dek_ref, kapabilitet_jti, promotert_ts)"
            " VALUES (%s,%s,%s,%s,%s,1,'kh',0,%s,64,%s,%s,%s,'k1',%s,"
            " CASE WHEN %s='promotert' THEN now() END)"
            " RETURNING artefakt_id",
            (ten, oid, at, mid, rel, tilstand, "ab" * 32, b"\x01" * 40,
             b"\x02" * 12, secrets.token_hex(12), tilstand)).fetchone()[0]

    ut = {"mid": mid, "ten": ten,
          "e2e": artefakt("r-kandidat", "promotert")}
    if promoter_paa_drillet:
        ut["e2e_drillet"] = artefakt("r-drillet", "promotert")
    if staged_paa_kandidat:
        ut["staged"] = artefakt("r-kandidat", "staged")
    m.commit()
    return ut


def _drill(m, mid, *, claim_stopp=True, rene=True, tilbake=True,
           nokkel=None):
    m.execute("SET ROLE disponit_modules_admin")
    did = m.execute(
        "SELECT registrer_moduldrill(%s,'staging','r-drillet','r-rullback',"
        "'r-kandidat',%s,%s,%s,%s,'test')",
        (mid, claim_stopp, rene, tilbake,
         nokkel or "n-" + secrets.token_hex(6))).fetchone()[0]
    # RESET FØR commit: en commit med SET ROLE stående gjør admin til
    # sesjonens «faste» rolle — enhver senere rollback faller da TILBAKE
    # til admin, og neste migrator-lesning dør på grants.
    m.execute("RESET ROLE")
    m.commit()
    return did


def _punkter(m, krav=KRAV):
    rader = m.execute("SELECT punkt FROM akseptkrav_punkt WHERE krav_id=%s",
                      (krav,)).fetchall()
    return {r[0]: {"grenseverdi": "0", "maalt_verdi": "0",
                   "kilde_type": "ci_kjoring", "kilde_ref": "run test"}
            for r in rader}


def _aksepter(m, k, did, *, release="r-kandidat", artefakt=None,
              punkter=None, nokkel=None, miljo="staging"):
    m.execute("RESET ROLE")     # forrige _aksepter kan ha etterlatt admin
    if punkter is None:
        punkter = _punkter(m)   # leses som migrator — admin har ikke SELECT
    m.execute("SET ROLE disponit_modules_admin")
    m.execute(
        "SELECT aksepter_moduldeployment(%s,%s,%s,%s,%s,%s,%s::uuid,'e-sha',"
        "'m-commit','run-1','ci-sha',%s::jsonb,%s,'test')",
        (k["mid"], miljo, release, did, KRAV, k["ten"],
         artefakt or k["e2e"],
         json.dumps(punkter),
         nokkel or "a-" + secrets.token_hex(6)))
    m.execute("RESET ROLE")   # aldri la admin bli sesjonens faste rolle


# ---------------------------------------------------------------------------

@pg
def test_akseptflyten_ende_til_ende(migrator):
    """Positiv kontroll: drill → aksept → hendelse + komplett punktsett +
    registerhendelse. Alt annet i fila er avvisninger av varianter av
    dette — uten den positive veien måler de ingenting."""
    k = _kjede(migrator)
    did = _drill(migrator, k["mid"])
    _aksepter(migrator, k, did)
    migrator.commit()
    migrator.execute("RESET ROLE")
    n_h, n_p = migrator.execute(
        "SELECT (SELECT count(*) FROM modulaksept WHERE modul_id=%s),"
        "       (SELECT count(*) FROM modulaksept_punkt WHERE modul_id=%s)",
        (k["mid"], k["mid"])).fetchone()
    krav_n = migrator.execute(
        "SELECT count(*) FROM akseptkrav_punkt WHERE krav_id=%s",
        (KRAV,)).fetchone()[0]
    hend = migrator.execute(
        "SELECT count(*) FROM modulregister_hendelse WHERE modul_id=%s"
        " AND hendelse='modulaksept'", (k["mid"],)).fetchone()[0]
    migrator.rollback()
    assert (n_h, n_p, hend) == (1, krav_n, 1)


@pg
def test_aksept_uten_drill_avvises(migrator):
    """Port 1: drill_id som ikke finnes → FK, navngitt constraint."""
    k = _kjede(migrator)
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as ei:
        _aksepter(migrator, k, 999999999)
    migrator.rollback()
    assert "modulaksept" in str(ei.value)


@pg
def test_drill_for_annen_deploymentrad_avvises(migrator):
    """Port 2 (A1): drillens akseptkandidat er r-kandidat; aksept av
    r-drillet med samme drill → FK-avvist. Digestene er IDENTISKE på
    alle releasene — porten beviser at identiteten bærer, ikke bytene."""
    k = _kjede(migrator, promoter_paa_drillet=True)
    did = _drill(migrator, k["mid"])
    # r-drillet er draining → claiming-porten i funksjonen treffer først;
    # det er samme dom («aksepten binder raden som faktisk kjører»), og
    # FK-en står bak den for enhver annen skrivevei.
    with pytest.raises((psycopg.errors.ForeignKeyViolation,
                        psycopg.errors.InvalidParameterValue)):
        _aksepter(migrator, k, did, release="r-drillet",
                  artefakt=k["e2e_drillet"])
    migrator.rollback()


@pg
def test_e2e_artefakt_fra_annen_release_avvises(migrator):
    """Port 3 (A2): gyldig, promotert artefakt — fra FEIL release
    (prod-formen: 23 r1-artefakter mot 1 r5). Delt release_id i FK-en
    feller det."""
    k = _kjede(migrator, promoter_paa_drillet=True)
    did = _drill(migrator, k["mid"])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _aksepter(migrator, k, did, artefakt=k["e2e_drillet"])
    migrator.rollback()


@pg
def test_e2e_artefakt_som_ikke_er_promotert_avvises(migrator):
    """Port 4 (E1f): tilstanden står I den refererte nøkkelen — et
    staged artefakt kan ikke bære aksepten, og resultatlåsen gjør
    'promotert' varig."""
    k = _kjede(migrator, staged_paa_kandidat=True)
    did = _drill(migrator, k["mid"])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _aksepter(migrator, k, did, artefakt=k["staged"])
    migrator.rollback()


@pg
def test_ufullstendig_punktsett_gir_ingen_hendelse(migrator):
    """Port 5 (A3): mangler ETT punkt → ingenting skrives — hendelsen og
    punktene er én transaksjon, og kravregisteret i basen definerer
    «komplett», ikke kallerens liste."""
    k = _kjede(migrator)
    did = _drill(migrator, k["mid"])
    punkter = _punkter(migrator)
    fjernet = sorted(punkter)[0]
    del punkter[fjernet]
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, k, did, punkter=punkter)
    migrator.rollback()
    assert fjernet in str(ei.value)
    # ... og et punkt uten alle fire feltene er også ufullstendig.
    punkter = _punkter(migrator)
    punkter[fjernet] = {"grenseverdi": "0"}
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _aksepter(migrator, k, did, punkter=punkter)
    migrator.rollback()
    n = migrator.execute("SELECT count(*) FROM modulaksept WHERE"
                         " modul_id=%s", (k["mid"],)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "en ufullstendig aksept etterlot en hendelse"


@pg
def test_hendelse_drill_og_punkt_er_append_only(migrator):
    """Port 6: UPDATE/DELETE avvises på alle tre tabellene — også for
    migrator."""
    k = _kjede(migrator)
    did = _drill(migrator, k["mid"])
    _aksepter(migrator, k, did)
    migrator.commit()
    migrator.execute("RESET ROLE")
    for sql in (
            "UPDATE moduldrill SET tilbake_ok=false WHERE modul_id=%s",
            "DELETE FROM moduldrill WHERE modul_id=%s",
            "UPDATE modulaksept SET release_id='x' WHERE modul_id=%s",
            "DELETE FROM modulaksept WHERE modul_id=%s",
            "UPDATE modulaksept_punkt SET maalt_verdi='9' WHERE modul_id=%s",
            "DELETE FROM modulaksept_punkt WHERE modul_id=%s"):
        with pytest.raises(psycopg.errors.RaiseException):
            migrator.execute(sql, (k["mid"],))
        migrator.rollback()


@pg
def test_drill_med_roedt_kontrollpunkt_baerer_ingen_aksept(migrator):
    """Port 7 (E1f/SP-9): utfallene står i den refererte nøkkelen, så en
    drill med ett rødt punkt kan REGISTRERES (ærlig historie) men aldri
    REFERERES av en aksept."""
    k = _kjede(migrator)
    for felt in ("claim_stopp", "rene", "tilbake"):
        did = _drill(migrator, k["mid"], **{felt: False})
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _aksepter(migrator, k, did)
        migrator.rollback()


@pg
def test_replay_gir_en_hendelse_og_en_drill(migrator):
    """Port 8 (SP-2): samme nøkkel → samme rad; drillnøkkel gjenbrukt med
    ANNET innhold → høylytt avvist."""
    k = _kjede(migrator)
    nk = "n-" + secrets.token_hex(6)
    d1 = _drill(migrator, k["mid"], nokkel=nk)
    d2 = _drill(migrator, k["mid"], nokkel=nk)
    assert d1 == d2
    ak = "a-" + secrets.token_hex(6)
    _aksepter(migrator, k, d1, nokkel=ak)
    _aksepter(migrator, k, d1, nokkel=ak)   # no-op, ingen unik-kollisjon
    migrator.commit()
    migrator.execute("RESET ROLE")
    n = migrator.execute("SELECT count(*) FROM modulaksept WHERE"
                         " modul_id=%s", (k["mid"],)).fetchone()[0]
    migrator.rollback()
    assert n == 1
    k2 = _kjede(migrator)
    migrator.execute("SET ROLE disponit_modules_admin")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        migrator.execute(
            "SELECT registrer_moduldrill(%s,'staging','r-drillet',"
            "'r-rullback','r-kandidat',true,true,true,%s,'test')",
            (k2["mid"], nk))
    migrator.rollback()


@pg
def test_ordinaere_roller_naar_ingenting(migrator):
    """Port 9: runtime har verken EXECUTE på funksjonene eller DML på
    tabellene — lesing er alt."""
    k = _kjede(migrator)
    rt = _rt()
    try:
        rt.execute("SELECT set_config('disponit.tenant',%s,true)",
                   (k["ten"],))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT registrer_moduldrill(%s,'staging','a','b',"
                       "'c',true,true,true,'n','x')", (k["mid"],))
        rt.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT aksepter_moduldeployment(%s,'staging','r',"
                       "1,'k','t',gen_random_uuid(),'e','m','r','c',"
                       "'{}'::jsonb,'n','x')", (k["mid"],))
        rt.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("INSERT INTO moduldrill (modul_id, miljo,"
                       " drillet_release, rullback_release,"
                       " akseptkandidat_release, epoch_snapshot,"
                       " digest_snapshot, claim_stopp_ok, rene_utfall_ok,"
                       " tilbake_ok, nokkel, aktor) VALUES (%s,'staging',"
                       "'a','b','c',0,'d',true,true,true,'n','x')",
                       (k["mid"],))
        rt.rollback()
        # ... men SELECT virker (statusflater leser registeret).
        rt.execute("SELECT count(*) FROM modulaksept")
        rt.rollback()
    finally:
        rt.close()


@pg
def test_digestporten_feller_andre_bytes(migrator):
    """A1s andre halvdel: en kandidat med ANNEN digest enn den drillede
    avvises av registreringen — aksepterte bytes er drillede bytes."""
    k = _kjede(migrator)
    migrator.execute("SELECT set_config('disponit.tenant',%s,false)",
                     (k["ten"],))
    # Egen kontrakt: `en_claiming_per_kontrakt` tillater bare én claiming
    # per kontrakt-hash, og kandidaten med andre bytes trenger sin egen.
    migrator.execute("INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
                     " kontrakt_hash, payload_schema_hash,"
                     " kvittering_schema_hash, sideeffektklasse,"
                     " reversibilitet) VALUES (%s,2,'kh2','ph','qh',"
                     "'ekstern_lesing','direkte')", (k["mid"],))
    migrator.execute("INSERT INTO modulrelease (modul_id, release_id,"
                     " kontraktversjon, kontrakt_hash, manifest_hash,"
                     " artifact_digest) VALUES (%s,'r-andre',2,'kh2','mh',"
                     "'digest-ANNEN')", (k["mid"],))
    migrator.execute("INSERT INTO moduldeployment (modul_id, release_id,"
                     " kontraktversjon, kontrakt_hash, miljo, livslop)"
                     " SELECT %s,'r-andre',2,'kh2','staging','claiming'",
                     (k["mid"],))
    migrator.commit()
    migrator.execute("SET ROLE disponit_modules_admin")
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        migrator.execute(
            "SELECT registrer_moduldrill(%s,'staging','r-drillet',"
            "'r-rullback','r-andre',true,true,true,%s,'test')",
            (k["mid"], "n-" + secrets.token_hex(6)))
    migrator.rollback()
    assert "digest" in str(ei.value)


@pg
def test_aksept_gjelder_en_deploymentrad(migrator):
    """Port 14: hendelsen for (staging, X) autoriserer ikke
    (produksjon, X) — hver rad krever sin egen aksept med egen drill."""
    k = _kjede(migrator)
    did = _drill(migrator, k["mid"])
    _aksepter(migrator, k, did)
    migrator.commit()
    migrator.execute("RESET ROLE")
    with pytest.raises((psycopg.errors.InvalidParameterValue,
                        psycopg.errors.ForeignKeyViolation)):
        _aksepter(migrator, k, did, miljo="produksjon")
    migrator.rollback()


# ---------------------------------------------------------------------------
# Evidensapparatet og innholdet (portene 10–13) — statiske
# ---------------------------------------------------------------------------

@pg
def test_kravet_er_registrert_og_punktene_bundet(migrator):
    """Port 10: `wcag-kontroll-v1` står i KRAVGRENSER, kravpunktregisteret
    bærer §12-settet, og HVERT m56-sjekklistepunkt er `ja` MED
    krav_id+artefakt+sha+bevismaalinger — et `ja` uten binding er usynlig
    for evidensporten og skal ikke finnes i dette manifestet."""
    import yaml

    from manifestskjema import KRAVGRENSER, valider_artefakter
    assert "wcag-kontroll-v1" in KRAVGRENSER
    assert "rollback-m56-v1" in KRAVGRENSER
    n = migrator.execute("SELECT count(*) FROM akseptkrav_punkt WHERE"
                         " krav_id='wcag-kontroll-v1'").fetchone()[0]
    migrator.rollback()
    assert n == 21, f"kravpunktregisteret har {n} punkter, §12 har 21"
    man = yaml.safe_load(
        (ROT / "platform/modules/m56_wcag_audit/manifest.yaml").read_text(
            encoding="utf-8"))
    for navn, p in man["staging_sjekkliste"].items():
        if not isinstance(p, dict):
            continue
        assert p.get("status") == "ja", f"{navn} er ikke ja"
        for felt in ("krav_id", "artefakt", "artefakt_sha256",
                     "bevismaalinger"):
            assert p.get(felt), f"{navn} mangler {felt}"
    assert valider_artefakter(man) == []
    # Flippet er UTSATT (dokumentert avvik): registerets konsistensregel
    # nekter en aktiv modul å avhenge av m02_revisjonslogg
    # (under_utvikling). Porten her måler at utsettelsen er DOKUMENTERT i
    # manifestet — og fjernes den (m02-aksept-arcen), skal disse to byttes
    # til aktiv/produksjon-assertene.
    assert man["status"] == "under_utvikling"
    assert man["driftstilstand"] == "ikke_i_drift"
    hode = (ROT / "platform/modules/m56_wcag_audit/manifest.yaml"
            ).read_text(encoding="utf-8")
    assert "m02" in hode and "konsistensregel" in hode


@pg
def test_evidenskjeden_er_bytebundet_hele_veien():
    """Port 11 (SP-11): manifestet binder sammendraget med sha256,
    sammendraget binder råfilen (`kilde_sha256`), og sammendraget kan
    REGENERERES mekanisk av den innsjekkede råfilen — et bytte i noe
    ledd bryter kjeden her, i CI."""
    art_sti = ROT / ("deploy/staging/artefakter/"
                     "wcag-kontroll-v1-20260818T200413.json")
    art = json.loads(art_sti.read_text(encoding="utf-8"))
    kilde = ROT / art["oppsett"]["kilde"]
    assert hashlib.sha256(kilde.read_bytes()).hexdigest() == \
        art["oppsett"]["kilde_sha256"], "råfilen er ikke den artefaktet binder"
    r = subprocess.run(
        [sys.executable, str(ROT / "deploy/staging/wcag-kontroll-artefakt.py"),
         str(kilde)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == art, \
        "sammendraget lar seg ikke regenerere fra råfilen — utvalgsreglene" \
        " og artefaktet har glidd fra hverandre"


@pg
def test_planlinjen_og_etiketten_fulgte_flippet():
    """Port 12: planlinjen står i M-56-flyten FØRST NÅ (048 leverte
    scheduleren), og katalogens etikett er avledet — ikke hardkodet
    (manifest-bindingen måles av test_ui_kontrakt; her måles selve
    innholdet)."""
    v8 = (ROT / "docs/spesifikasjon/disponit-prototype-v8.html").read_text(
        encoding="utf-8")
    assert "Mottar bestilling gjennom beslutningsveien, eller fra en"
    assert ("Mottar bestilling gjennom beslutningsveien, eller fra en"
            " aktiv plan") in v8
    ui = (ROT / "platform/core/ui/static/js/plattformdata.js").read_text(
        encoding="utf-8")
    blokk = re.search(r"export const MODULSTATUS = \{(.*?)\n\};", ui, re.S)
    # «bygges» til m02-aksepten flipper manifestet — etiketten er avledet,
    # og test_ui_kontrakt binder den mot manifestaksene begge veier.
    assert re.search(r'56:\s*"bygges"', blokk.group(1))


@pg
def test_sp10_daekker_049():
    """Port 13: begge SP-10-kjøringene står i CI og 049 har registrert
    seed+måling (bebodd base med promoterte artefakter på to releaser)."""
    ci = (ROT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert re.search(r"sp10-provekjoring\.py 49\b", ci)
    sp10 = (ROT / "deploy/staging/sp10-provekjoring.py").read_text(
        encoding="utf-8")
    assert "49: (_seed_049, _mal_049)" in sp10
    assert M049.exists()
