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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, dekker, migrator, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]
M049 = ROT / "platform/core/db/migrations/049_modulaksept.sql"
KRAV = "wcag-kontroll-v1"
SHA0 = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
#: Drillens MÅLETID — artefaktets egen `ts`, ikke innskrivingstiden.
#: Fast og i fortiden med vilje: en test som sender `now()` ville ikke
#: kunne skille de to tidspunktene fra hverandre.
DRILL_TS = datetime(2026, 8, 20, 13, 22, 6, tzinfo=timezone.utc)
#: E2E-artefaktet kandidaten promoterte i drillen — identiteten aksepten
#: skal binde, ikke bare «ett promotert artefakt fra samme release».
E2E_UUID = "ad1579e2-0000-4000-8000-000000000000"
#: sha256 av drillartefaktets bytes — raden skal NAVNGI bevisfilen den
#: hviler på, selv om basen aldri kan lese den (Codex P1, runde 5).
DRILL_SHA = "11" * 32


def _rt():
    from db.pg import koble
    return koble(DSN)


def _kjede(m, *, promoter_paa_drillet=False, staged_paa_kandidat=False):
    """Full modulkjede for én test. -> dict med identitetene.

    Tre deployments (drenert, drenert, claiming) og de TRE drilloppdragene
    med evidensen basen måler kontrollpunktutfallene på.

    Codex' P1 på PR #117 (runde 5): `registrer_moduldrill` tok utfallene
    som boolske parametre, så en kaller med `disponit_modules_admin` —
    den brede deployfullmakten — kunne skrive en grønn, immutabel drillrad
    uten å ha kjørt noe som helst. Funksjonen MÅLER dem nå i
    `oppdrag`/`artefakt`, og fixturet bygger derfor formen en ekte drill
    etterlater:

      inflight  — utført MED signert kvittering, promotert artefakt på den
                  DRILLEDE releasen (utfall og evidens stemmer: rent utfall)
      rullback  — ingen artefakter på den drillede (claim-stoppet holdt),
                  promotert på rullbakk-releasen (rullbakken ble bootet og
                  gjorde arbeid)
      kandidat  — promotert på kandidatreleasen; dét artefaktet er
                  akseptens E2E-bevis
    """
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

    def oppdrag(*, status="utfort", kvittering=True, eier=None):
        blid = m.execute(
            "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
            " beslutning, begrunnelse, idempotency_key, kilde) VALUES"
            " (%s,'h','p','TILLAT','[]'::jsonb,%s,'arbeidskapabilitet')"
            " RETURNING id", (ten, secrets.token_hex(8))).fetchone()[0]
        return m.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            " handling, eiermodul, status, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, koblingsstatus,"
            " beslutning_loggpost_id, kvittering) VALUES ('beslutning',%s,"
            "'kontroll.wcag.nettsted','kontroll.wcag.nettsted',%s,%s,"
            "%s,'k1',%s, now()+interval '1 hour', now()+interval '2 hours',"
            "'KOBLET',%s,%s::jsonb) RETURNING id",
            (ten, eier or mid, status, b"\x00" * 24, b"\x00" * 12, blid,
             '{"signatur":"s"}' if kvittering else None)).fetchone()[0]

    def artefakt(rel, tilstand, oid=None):
        oid = oppdrag() if oid is None else oid
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

    inflight, rullback, kandidat = oppdrag(), oppdrag(), oppdrag()
    artefakt("r-drillet", "promotert", inflight)
    artefakt("r-rullback", "promotert", rullback)
    ut = {"mid": mid, "ten": ten, "at": at,
          "opp": {"inflight": inflight, "rullback": rullback,
                  "kandidat": kandidat},
          "oppdrag": oppdrag, "artefakt": artefakt,
          "e2e": artefakt("r-kandidat", "promotert", kandidat)}
    if promoter_paa_drillet:
        ut["e2e_drillet"] = artefakt("r-drillet", "promotert", kandidat)
    if staged_paa_kandidat:
        ut["staged"] = artefakt("r-kandidat", "staged", kandidat)
    m.commit()
    return ut


def _drill(m, k, *, nokkel=None, utfort_ts=None, opp=None, sha=None,
           epoch=0):
    """Registrerer drillen for kjeden `k`. -> drill_id.

    Utfallene er ikke parametre lenger (Codex P1, #117 runde 5): kallet
    oppgir HVA drillen ble målt på, og funksjonen måler selv.
    """
    o = opp or k["opp"]
    m.execute("SET ROLE disponit_modules_admin")
    did = m.execute(
        "SELECT registrer_moduldrill(%s,'staging','r-drillet','r-rullback',"
        "'r-kandidat',%s,%s,%s,%s,%s,%s,%s,'test',%s)",
        (k["mid"], k["ten"], o["inflight"], o["rullback"], o["kandidat"],
         epoch, sha or DRILL_SHA, nokkel or "n-" + secrets.token_hex(6),
         utfort_ts or DRILL_TS)).fetchone()[0]
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
              punkter=None, nokkel=None, miljo="staging",
              evidens_sha="e-sha", ci_run="run-1"):
    m.execute("RESET ROLE")     # forrige _aksepter kan ha etterlatt admin
    if punkter is None:
        punkter = _punkter(m)   # leses som migrator — admin har ikke SELECT
    m.execute("SET ROLE disponit_modules_admin")
    m.execute(
        "SELECT aksepter_moduldeployment(%s,%s,%s,%s,%s,%s,%s::uuid,%s,"
        "'m-commit',%s,'ci-sha',%s::jsonb,%s,'test')",
        (k["mid"], miljo, release, did, KRAV, k["ten"],
         artefakt or k["e2e"], evidens_sha, ci_run,
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
    did = _drill(migrator, k)
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
    did = _drill(migrator, k)
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
    did = _drill(migrator, k)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _aksepter(migrator, k, did, artefakt=k["e2e_drillet"])
    migrator.rollback()


@pg
def test_e2e_artefakt_som_ikke_er_promotert_avvises(migrator):
    """Port 4 (E1f): tilstanden står I den refererte nøkkelen — et
    staged artefakt kan ikke bære aksepten, og resultatlåsen gjør
    'promotert' varig."""
    k = _kjede(migrator, staged_paa_kandidat=True)
    did = _drill(migrator, k)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _aksepter(migrator, k, did, artefakt=k["staged"])
    migrator.rollback()


@pg
def test_ufullstendig_punktsett_gir_ingen_hendelse(migrator):
    """Port 5 (A3): mangler ETT punkt → ingenting skrives — hendelsen og
    punktene er én transaksjon, og kravregisteret i basen definerer
    «komplett», ikke kallerens liste."""
    k = _kjede(migrator)
    did = _drill(migrator, k)
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
    did = _drill(migrator, k)
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
    # Rødt punkt er nå EVIDENS som ikke bærer utfallet — funksjonen måler
    # selv (Codex P1, runde 5), så en rød drill lages ved å gi den den
    # formen en mislykket drill faktisk etterlater.
    roedt_claim = k["oppdrag"]()          # den DRENERTE releasen tok det
    k["artefakt"]("r-drillet", "promotert", roedt_claim)
    varianter = (
        # (a) claim-stoppet holdt ikke
        ("claim_stopp", {**k["opp"], "rullback": roedt_claim},
         psycopg.errors.ForeignKeyViolation),
        # (b) `utfort` uten promotert evidens — falskt verdikt
        ("rene", {**k["opp"], "inflight": k["oppdrag"]()},
         psycopg.errors.ForeignKeyViolation),
        # (c) kandidaten promoterte aldri. `tilbake_ok` og E2E-porten
        # måler her SAMME faktum, så finnes det ingen gyldig drill finnes
        # det heller ikke noe gyldig E2E-bevis: porten foran FK-en svarer
        # først. Begge dommene er avvisning, og FK-en står bak uansett.
        ("tilbake", {**k["opp"], "kandidat": k["oppdrag"]()},
         (psycopg.errors.ForeignKeyViolation,
          psycopg.errors.InvalidParameterValue)),
    )
    migrator.commit()
    for _navn, opp, feil in varianter:
        did = _drill(migrator, k, opp=opp)
        with pytest.raises(feil):
            _aksepter(migrator, k, did)
        migrator.rollback()


@pg
def test_replay_gir_en_hendelse_og_en_drill(migrator):
    """Port 8 (SP-2): samme nøkkel → samme rad; drillnøkkel gjenbrukt med
    ANNET innhold → høylytt avvist."""
    k = _kjede(migrator)
    nk = "n-" + secrets.token_hex(6)
    d1 = _drill(migrator, k, nokkel=nk)
    d2 = _drill(migrator, k, nokkel=nk)
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
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _drill(migrator, k2, nokkel=nk)
    migrator.rollback()


@pg
def test_replay_med_andre_bevis_avvises(migrator):
    """Codex' P2 på PR #117: en akseptnøkkel gjenbrukt med RETTEDE bevis
    (ny CI-kjøring, ny evidenshash, andre punktmålinger) returnerte
    stille, og skriptet skrev AKSEPTERT mens den immutable raden fortsatt
    bar de gamle bevisene. Rettelsen skal høres, ikke forsvinne —
    uendret replay er fortsatt et no-op."""
    k = _kjede(migrator)
    did = _drill(migrator, k)
    ak = "a-" + secrets.token_hex(6)
    _aksepter(migrator, k, did, nokkel=ak)
    migrator.commit()
    for endring in ({"ci_run": "run-2"}, {"evidens_sha": "e-sha-rettet"}):
        migrator.execute("RESET ROLE")
        with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
            _aksepter(migrator, k, did, nokkel=ak, **endring)
        assert "annet innhold" in str(ei.value)
        migrator.rollback()
    migrator.execute("RESET ROLE")
    p = _punkter(migrator)
    rettet = sorted(p)[0]
    p[rettet] = dict(p[rettet], maalt_verdi="1")
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, k, did, nokkel=ak, punkter=p)
    assert rettet in str(ei.value)
    migrator.rollback()
    _aksepter(migrator, k, did, nokkel=ak)      # identisk → no-op
    migrator.commit()
    migrator.execute("RESET ROLE")
    n = migrator.execute("SELECT count(*) FROM modulaksept WHERE modul_id=%s",
                         (k["mid"],)).fetchone()[0]
    ci = migrator.execute("SELECT ci_run FROM modulaksept WHERE modul_id=%s",
                          (k["mid"],)).fetchone()[0]
    migrator.rollback()
    assert (n, ci) == (1, "run-1")


@pg
def test_drillnokkel_med_andre_utfall_avvises(migrator):
    """Samme klasse i drillen: nøkkelkontrollen leste bare modul, miljø,
    drillet og kandidat — et replay med ANNET innhold fikk den grønne
    raden tilbake.

    Etter runde 5 er utfallene ikke lenger kallerens, så det MATERIELLE i
    et drillkall er hva drillen ble målt på: tenanten, de tre oppdragene
    og bytene i artefaktet. Et replay som bytter noen av dem er en annen
    drill og skal høres."""
    k = _kjede(migrator)
    nk = "n-" + secrets.token_hex(6)
    _drill(migrator, k, nokkel=nk)
    andre = {ledd: {**k["opp"], ledd: k["oppdrag"]()}
             for ledd in ("inflight", "rullback", "kandidat")}
    migrator.commit()
    for opp in andre.values():
        migrator.execute("RESET ROLE")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _drill(migrator, k, nokkel=nk, opp=opp)
        migrator.rollback()
    migrator.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _drill(migrator, k, nokkel=nk, sha="22" * 32)
    migrator.rollback()
    migrator.execute("RESET ROLE")
    migrator.execute("SET ROLE disponit_modules_admin")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        migrator.execute(
            "SELECT registrer_moduldrill(%s,'staging','r-drillet',"
            "'r-annen-rullback','r-kandidat',%s,%s,%s,%s,0,%s,%s,"
            "'test',%s)",
            (k["mid"], k["ten"], k["opp"]["inflight"], k["opp"]["rullback"],
             k["opp"]["kandidat"], DRILL_SHA, nk, DRILL_TS))
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
                       "'c',%s,1,2,3,0,%s,'n','x',%s)",
                       (k["mid"], k["ten"], DRILL_SHA, DRILL_TS))
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
                       " digest_snapshot, tenant, inflight_oppdrag,"
                       " rullback_oppdrag, kandidat_oppdrag,"
                       " artefakt_sha256, claim_stopp_ok, rene_utfall_ok,"
                       " tilbake_ok, nokkel, aktor, utfort_ts) VALUES"
                       " (%s,'staging','a','b','c',0,'d',%s,1,2,3,%s,"
                       "true,true,true,'n','x',%s)",
                       (k["mid"], k["ten"], DRILL_SHA, DRILL_TS))
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
            "'r-rullback','r-andre',%s,%s,%s,%s,0,%s,%s,'test',%s)",
            (k["mid"], k["ten"], k["opp"]["inflight"], k["opp"]["rullback"],
             k["opp"]["kandidat"], DRILL_SHA,
             "n-" + secrets.token_hex(6), DRILL_TS))
    migrator.rollback()
    assert "digest" in str(ei.value)


@pg
def test_aksept_gjelder_en_deploymentrad(migrator):
    """Port 14: hendelsen for (staging, X) autoriserer ikke
    (produksjon, X) — hver rad krever sin egen aksept med egen drill."""
    k = _kjede(migrator)
    did = _drill(migrator, k)
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
        # Et punkt er enten BUNDET eller BLOKKERT med en grunn — aldri
        # bare fjernet. `rollback_testet` er blokkert til drillen er
        # kjørt på nytt (Codex P1, #117 runde 3): kjøringen 13:22 bootet
        # aldri rullback-releasen, så artefaktet måler ikke det
        # rullbakkbeviset skal måle.
        if p.get("status") == "blokkert":
            assert p.get("blokkert_av"), f"{navn} er blokkert av ingenting"
            assert not p.get("artefakt"), \
                f"{navn} er blokkert, men bærer fortsatt en binding"
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


def _runde_skript():
    """Sammendragsgeneratoren lastet som modul (filnavnet har bindestrek)."""
    import importlib.util
    sti = ROT / "deploy/staging/wcag-kontroll-artefakt.py"
    spec = importlib.util.spec_from_file_location("wcag_artefakt", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sammendraget_maa_komme_fra_en_sammenhengende_kjoring():
    """Codex' P1 (runde 4): hver måling ble plukket UAVHENGIG som «siste
    av sin type», mens release og image ble hentet fra konteksten før
    siste `fase5_resultat`. En delvis gjenkjøring av bare robots — etter
    et image- eller releasebytte — ble derfor satt sammen med et eldre
    fase 5-resultat og tilskrevet den GAMLE digesten. Alle `ok`-feltene
    kunne stå grønne uten at én kjøring hadde produsert tallsettet."""
    m = _runde_skript()
    art = json.loads((ROT / ("deploy/staging/artefakter/"
                             "wcag-kontroll-v1-20260818T200413.json")
                      ).read_text(encoding="utf-8"))
    kilde = ROT / art["oppsett"]["kilde"]
    rader = m.les(kilde)
    # Den innsjekkede fila ER én sammenhengende kontekst — porten er
    # ikke bare streng, den er riktig.
    assert m.sammendrag(rader, art["oppsett"]["kilde"],
                        art["oppsett"]["kilde_sha256"]) == art

    robots = [d for d in rader if d["hendelse"] == "port20_robots"][-1]
    fremmed = "c" * 64
    assert art["oppsett"]["image_digest"] != fremmed

    def _sammendrag(ekstra):
        return m.sammendrag(rader + ekstra, art["oppsett"]["kilde"],
                            art["oppsett"]["kilde_sha256"])

    # Selve angrepet: nytt image rullet ut, så BARE robots kjørt om
    # igjen. Fase 5, frekvens, motor og feilinjisering er de gamle.
    paa_nytt_image = [
        {"ts": "2026-08-19T09:00:00+00:00", "hendelse": "fase1_ok",
         "image_id": f"sha256:{fremmed}"},
        dict(robots, ts="2026-08-19T09:00:01+00:00"),
    ]
    with pytest.raises(SystemExit) as ei:
        _sammendrag(paa_nytt_image)
    assert "ULIKE kjøringer" in str(ei.value)

    # Samme image, ny release: konteksten er like fullt en annen.
    paa_ny_release = [
        {"ts": "2026-08-19T09:00:00+00:00", "hendelse": "fase2_ok",
         "release": "wcag-r99", "artifact_digest": art["oppsett"][
             "image_digest"], "claiming": True, "kontrakt_hash": SHA0,
         "kvittering_hash": SHA0, "payload_hash": SHA0},
        dict(robots, ts="2026-08-19T09:00:01+00:00"),
    ]
    with pytest.raises(SystemExit) as ei:
        _sammendrag(paa_ny_release)
    assert "ULIKE kjøringer" in str(ei.value)

    # …men en HEL gjenkjøring på det nye imaget er en gyldig runde, og
    # da er det den nye digesten som skrives — ikke den gamle.
    typer = ("fase5_resultat", "port20_robots", "port20_robots_5xx",
             "port21_frekvens", "port24_motormiljo",
             "feilinjisering_motorfeil", "feilinjisering_evidensfrist")
    hel = [{"ts": "2026-08-19T09:00:00+00:00", "hendelse": "fase1_ok",
            "image_id": f"sha256:{fremmed}"}]
    for n, navn in enumerate(typer):
        siste = [d for d in rader if d["hendelse"] == navn
                 and d.get("utfall") != "tomt"][-1]
        hel.append(dict(siste, ts=f"2026-08-19T09:00:{n + 1:02d}+00:00"))
    ny = _sammendrag(hel)
    assert ny["oppsett"]["image_digest"] == fremmed
    assert ny["oppsett"]["release"] == \
        [d for d in rader if d["hendelse"] == "fase2_ok"][-1]["release"]
    assert ny["maalt"] == art["maalt"] and ny["bestatt"] is True


def test_wcag_grensene_maaler_at_portene_faktisk_kjorte():
    """Codex' to P2 på PR #117: en port som ikke ble prøvd, og et tak som
    ble brutt, passerte begge. `robots_5xx: 0 av 0` er fravær av en
    kontroll — likheten var sann fordi ingenting ble målt — og
    `frekvens_tillat` hadde bare et MINIMUM på 4, så en kjøring som
    slapp gjennom fem forespørsler over et tak på fire var «bestått»
    fordi den sjette ble avvist."""
    from manifestskjema import _sjekk_grenser
    ekte = json.loads((ROT / ("deploy/staging/artefakter/"
                              "wcag-kontroll-v1-20260818T200413.json")
                       ).read_text(encoding="utf-8"))
    assert _sjekk_grenser(KRAV, ekte) == []

    def _mutert(**felt):
        return dict(ekte, maalt=dict(ekte["maalt"], **felt))

    upravd = _mutert(robots_5xx_sider_kontrollert=0, robots_5xx_krav=0)
    assert any("robots_5xx_krav" in f for f in _sjekk_grenser(KRAV, upravd)), \
        "0 av 0 kontrollerte 5xx-sider slapp gjennom porten"
    over = _mutert(frekvens_tillat=5, frekvens_avvist_over_grense=1)
    assert any("frekvens_tillat" in f for f in _sjekk_grenser(KRAV, over)), \
        "en kjøring som utførte en forespørsel over taket ble godtatt"
    # Under grensen er fortsatt umålt, og ulikhet i 5xx står ved lag.
    assert _sjekk_grenser(KRAV, _mutert(frekvens_tillat=3))
    assert _sjekk_grenser(KRAV, _mutert(robots_5xx_sider_kontrollert=0))
    # Codex' P2 (runde 2): 11 signerte av 10 kjørte er ikke et strengere
    # bevis, det er et tall som ikke stemmer med seg selv — og
    # akseptraden ville båret «11/10» som om det var bestått.
    umulig = _mutert(kjoringer_signert_innen_frist=11, kjoringer_krav=10)
    assert any("kjoringer_signert_innen_frist" in f
               for f in _sjekk_grenser(KRAV, umulig)), \
        "flere signerte kjøringer enn kjørte slapp gjennom porten"
    assert _sjekk_grenser(KRAV, _mutert(kjoringer_signert_innen_frist=9))


def _superseder_drill():
    """Drillkjøringen 2026-08-20 13:22, slik den ligger innsjekket.

    Den bootet ALDRI rullback-releasen (Codex P1, runde 3), så den bærer
    ingen (b2)-måling. Filen blir liggende som historikk; manifestet
    binder den ikke lenger.
    """
    return json.loads((ROT / ("deploy/staging/artefakter/"
                              "rollback-m56-v1-20260820T132200.json")
                       ).read_text(encoding="utf-8"))


def _drillartefakt(**maalt):
    """Et KOMPLETT drillartefakt — formen det rettede skriptet skriver.

    Den superseder kjøringens tall, pluss de målingene den manglet: at
    rullbakken faktisk BOOTET og gjorde arbeid, og evidenstellingen bak
    det løpende oppdragets utfall.
    """
    ekte = _superseder_drill()
    komplett = dict(ekte["maalt"], inflight_promoterte_artefakter=1,
                    rullback_claimet_oppdrag=1,
                    rullback_promoterte_artefakter=1,
                    rullback_overtakelse_s=18.4)
    komplett.update(maalt)
    ident = {"inflight_oppdrag_id": "54",
             "inflight_artefakter": [E2E_UUID.replace("ad", "aa", 1)],
             "rullback_oppdrag_id": "55",
             "rullback_artefakter": [E2E_UUID.replace("ad", "ab", 1)],
             "kandidat_oppdrag_id": "56",
             "kandidat_artefakter": [E2E_UUID]}
    for felt, telling in (("inflight_artefakter",
                           "inflight_promoterte_artefakter"),
                          ("rullback_artefakter",
                           "rullback_promoterte_artefakter"),
                          ("kandidat_artefakter",
                           "kandidat_promoterte_artefakter")):
        # Identitetene FØLGER tallene: en test som muterer et antall skal
        # ikke måtte huske å mutere listen for at porten skal se det.
        ident[felt] = [f"{ident[felt][0][:-2]}{i:02x}"
                       for i in range(komplett[telling])]
    return dict(ekte, maalt=komplett, identiteter=ident,
                etterkontroll=dict(ekte["etterkontroll"],
                                   rullback_livslop="draining"))


def test_rullbakken_maa_ha_bootet_og_gjort_arbeid():
    """Codex' P1 (runde 3): drillen oppdaterte bare `moduldeployment` og
    startet så kandidaten, så `rullback_id` ble aldri bootet. En forrige
    release som ikke lar seg kjøre på verten eller mot basen ga da et
    grønt rullbakkbevis, målt utelukkende på at den gamle arbeideren
    sluttet å claime. Porten krever nå (b2): rullbakken plukket og
    fullførte det ventende oppdraget, promoterte, og ble selv drenert da
    kandidaten overtok."""
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    drillkrav = "rollback-m56-v1"
    assert _sjekk_grenser(drillkrav, _drillartefakt()) == []
    for felt in ("rullback_claimet_oppdrag", "rullback_promoterte_artefakter"):
        assert any(felt in f for f in
                   _sjekk_grenser(drillkrav, _drillartefakt(**{felt: 0}))), \
            f"{felt}=0 slapp gjennom — rullbakken gjorde ingenting"
    uten_boot = _drillartefakt()
    uten_boot["etterkontroll"] = dict(uten_boot["etterkontroll"],
                                      rullback_livslop="claiming")
    assert any("rullback_livslop" in f
               for f in _sjekk_grenser(drillkrav, uten_boot))
    # Den superseder kjøringen skal IKKE bestå porten: den målte aldri
    # (b2), og fravær av en måling er ikke en bestått måling.
    gammel = _superseder_drill()
    assert _sjekk_grenser(drillkrav, gammel), \
        "drillen uten rullbakk-boot passerer fortsatt evidensporten"
    assert valider_artefaktformat(gammel, drillkrav), \
        "skjemaet krever fortsatt ikke (b2)-målingene"


def test_e2e_beviset_maa_vaere_det_drillen_saa():
    """Codex' P2 (runde 3): `--e2e-artefakt` gikk rett inn i den
    immutable akseptraden, og FK-en i 049 sjekker bare tenant, modul,
    release og promotert tilstand. Et hvilket som helst ANNET promotert
    artefakt fra kandidatreleasen passerte derfor — en skrivefeil bandt
    aksepten til noe drillen aldri så, fordi drillartefaktet bare bar et
    antall og ingen identitet."""
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    m = _aksept_skript()
    drill = _drillartefakt()
    assert m.verifiser_e2e_artefakt(drill, E2E_UUID.upper()) == \
        E2E_UUID.upper(), "uuid-skrivemåten er ikke en annen identitet"
    for feil_uuid in ("ad1579e2-0000-4000-8000-0000000000ff",
                      drill["identiteter"]["rullback_artefakter"][0]):
        with pytest.raises(SystemExit) as ei:
            m.verifiser_e2e_artefakt(drill, feil_uuid)
        assert "ikke blant artefaktene" in str(ei.value)
    tomt = dict(drill, identiteter=dict(drill["identiteter"],
                                        kandidat_artefakter=[]))
    with pytest.raises(SystemExit):
        m.verifiser_e2e_artefakt(tomt, E2E_UUID)
    # …og porten krever at tallet og listen er SAMME måling.
    drillkrav = "rollback-m56-v1"
    uenig = dict(drill, identiteter=dict(drill["identiteter"],
                                         kandidat_artefakter=[]))
    assert any("kandidat_artefakter" in f
               for f in _sjekk_grenser(drillkrav, uenig))
    dobbelt = _drillartefakt(kandidat_promoterte_artefakter=2)
    dobbelt["identiteter"]["kandidat_artefakter"] = [E2E_UUID, E2E_UUID]
    assert any("gjentakelser" in f
               for f in _sjekk_grenser(drillkrav, dobbelt))
    uten = dict(drill)
    uten.pop("identiteter")
    assert any("identiteter" in f for f in _sjekk_grenser(drillkrav, uten))
    assert valider_artefaktformat(uten, drillkrav), \
        "skjemaet krever fortsatt ikke identitetene"
    assert valider_artefaktformat(
        dict(drill, identiteter=dict(drill["identiteter"],
                                     kandidat_artefakter=["ikke-en-uuid"])),
        drillkrav), "skjemaet godtar en artefaktidentitet som ikke er uuid"


def test_manifestet_binder_ikke_den_supersederte_drillen():
    """…og den blokkerte bindingen er DOKUMENTERT, ikke bare fjernet: et
    punkt som stilltiende forsvant, ville sett ut som om kravet aldri
    fantes."""
    import yaml
    man = yaml.safe_load(
        (ROT / "platform/modules/m56_wcag_audit/manifest.yaml").read_text(
            encoding="utf-8"))
    p = man["staging_sjekkliste"]["rollback_testet"]
    assert p["status"] == "blokkert" and p.get("blokkert_av")
    assert "kjøres på nytt" in p["blokkert_av"].lower()
    assert "artefakt" not in p, \
        "et blokkert punkt skal ikke bære en artefaktbinding"
    assert (ROT / ("deploy/staging/artefakter/"
                   "rollback-m56-v1-20260820T132200.json")).exists(), \
        "historikken slettes ikke — den slutter å være bindende"


def test_falske_verdikter_er_en_motsigelse_begge_veier():
    """Codex' P2 (runde 2): drillen ga alltid `falske_verdikter=0` så
    snart utfallet ikke var `utfort` — en `feilet` jobb som LIKEVEL
    hadde promotert et artefakt ble talt som et rent utfall, og porten
    hadde ingen egen telling å regne motsigelsen ut fra. Nå måles
    evidensen bak utfallet, og motsigelsen regnes ut på nytt."""
    from manifestskjema import _sjekk_grenser
    drillkrav = "rollback-m56-v1"
    assert _sjekk_grenser(drillkrav, _drillartefakt()) == []

    def _mutert(**felt):
        return _drillartefakt(**felt)

    # Selve hullet: feilet jobb, promotert artefakt, «ingen falske».
    feilet_med_evidens = _mutert(inflight_utfall="feilet",
                                 inflight_promoterte_artefakter=1,
                                 falske_verdikter=0)
    assert any("falske_verdikter" in f
               for f in _sjekk_grenser(drillkrav, feilet_med_evidens)), \
        "en feilet jobb som promoterte evidens ble talt som rent utfall"
    # Den andre veien står også: utført uten evidens er like falskt.
    utfort_uten_evidens = _mutert(inflight_promoterte_artefakter=0,
                                  falske_verdikter=0)
    assert any("falske_verdikter" in f
               for f in _sjekk_grenser(drillkrav, utfort_uten_evidens))
    # Codex' P1 (runde 3): fraværet av tellingen ble TILGITT når utfallet
    # var `utfort` — «ingen motsigelse er det eneste 0 kan bety» — og det
    # var nøyaktig formen det innsjekkede artefaktet hadde. Et utfall
    # uten evidenstelling er umålt, uansett hvilket utfall det er.
    from manifestskjema import valider_artefaktformat
    for utfall in ("utfort", "feilet"):
        utelatt = _mutert(inflight_utfall=utfall)
        utelatt["maalt"].pop("inflight_promoterte_artefakter")
        assert any("inflight_promoterte_artefakter" in f
                   for f in _sjekk_grenser(drillkrav, utelatt)), utfall
        assert valider_artefaktformat(utelatt, drillkrav), \
            f"skjemaet godtar fortsatt {utfall} uten evidenstelling"
    # …og de to rene formene passerer.
    assert _sjekk_grenser(drillkrav,
                          _mutert(inflight_promoterte_artefakter=1)) == []
    assert _sjekk_grenser(drillkrav, _mutert(
        inflight_utfall="feilet", inflight_promoterte_artefakter=0,
        falske_verdikter=0)) == []


@pg
def test_drillen_baerer_sin_egen_maaletid(migrator):
    """Codex' P2 (runde 3): `utfort_ts` sto med `DEFAULT now()` og fikk
    aldri en verdi, så en drill kjørt timer eller dager før aksepten ble
    registrert som om den kjørte i akseptøyeblikket — ingen
    ferskhetskontroll kunne skille UTFØRELSE fra REGISTRERING. Måletiden
    er artefaktets, registreringstiden er basens, og en drill kan ikke ha
    kjørt fram i tid."""
    k = _kjede(migrator)
    did = _drill(migrator, k)
    migrator.execute("RESET ROLE")
    utfort, registrert = migrator.execute(
        "SELECT utfort_ts, registrert_ts FROM moduldrill WHERE modul_id=%s"
        " AND drill_id=%s", (k["mid"], did)).fetchone()
    migrator.rollback()
    assert utfort == DRILL_TS, "målingen ble overskrevet av innskrivingen"
    assert registrert > utfort, "de to tidspunktene er ikke skilt"
    # Fram i tid er en påstand om framtiden, ikke en måling.
    migrator.execute("SET ROLE disponit_modules_admin")
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _drill(migrator, k, nokkel="n-" + secrets.token_hex(6),
               utfort_ts=datetime.now(timezone.utc) + timedelta(hours=1))
    migrator.rollback()
    assert "fram i tid" in str(ei.value)
    # Samme nøkkel med en ANNEN kjørings måletid er to kjøringer.
    nk = "n-" + secrets.token_hex(6)
    _drill(migrator, k, nokkel=nk)
    migrator.commit()
    migrator.execute("SET ROLE disponit_modules_admin")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _drill(migrator, k, nokkel=nk,
               utfort_ts=DRILL_TS - timedelta(days=1))
    migrator.rollback()
    migrator.execute("RESET ROLE")


@pg
def test_admin_kan_ikke_skrive_gronn_drill_uten_arbeid(migrator):
    """Codex' P1 (runde 5): hele evidensapparatet lå i `m56-aksept.py`.

    `disponit_modules_admin` er den BREDE deployfullmakten — den brukes
    til `registrer_release`, `bytt_release`, onboarding og
    provisjonering — og den hadde EXECUTE på begge akseptdefinerne. En
    som holdt den kunne derfor la være å kjøre skriptet, kalle
    `registrer_moduldrill` direkte med tre håndskrevne `true`, og få en
    immutabel grønn drillrad uten å ha kjørt noen drill; deretter
    FK-refererte `aksepter_moduldeployment` den, og aksepten var et
    faktum. Et skript er ingen skranke for den som kan la være å bruke
    det.

    Utfallene MÅLES nå av definerne, i `oppdrag` og `artefakt`. Den som
    holder fullmakten kan fortsatt KALLE — men han får det utfallet
    radene bærer, og grønne rader lages bare av arbeid."""
    k = _kjede(migrator)
    # Tomme oppdrag: ingen artefakter, ingen kvittering — nøyaktig det en
    # angriper har når han ikke har kjørt drillen.
    tomme = {ledd: k["oppdrag"](status="opprettet", kvittering=False)
             for ledd in ("inflight", "rullback", "kandidat")}
    migrator.commit()
    did = _drill(migrator, k, opp=tomme)
    migrator.execute("RESET ROLE")
    utfall = migrator.execute(
        "SELECT claim_stopp_ok, rene_utfall_ok, tilbake_ok FROM moduldrill"
        " WHERE modul_id=%s AND drill_id=%s", (k["mid"], did)).fetchone()
    migrator.rollback()
    assert utfall == (False, False, False), \
        "funksjonen tror fortsatt på kalleren i stedet for å måle"
    # …og den røde raden bærer ingen aksept.
    with pytest.raises((psycopg.errors.ForeignKeyViolation,
                        psycopg.errors.InvalidParameterValue)):
        _aksepter(migrator, k, did)
    migrator.rollback()
    # Motprøven: den ekte drillformen gir grønt på alle tre.
    migrator.execute("RESET ROLE")
    ekte = _drill(migrator, k, nokkel="n-" + secrets.token_hex(6))
    migrator.execute("RESET ROLE")
    assert migrator.execute(
        "SELECT claim_stopp_ok, rene_utfall_ok, tilbake_ok FROM moduldrill"
        " WHERE modul_id=%s AND drill_id=%s",
        (k["mid"], ekte)).fetchone() == (True, True, True)
    migrator.rollback()


@pg
def test_drillraden_navngir_oppdragene_og_bevisfilen(migrator):
    """Samme funn, andre halvdel: raden skal kunne etterprøves.

    En drillrad uten referanse til arbeidet den målte, kan ingen regne
    etter i ettertid. Raden bærer nå tenanten, de tre oppdragene (FK-et,
    så de MÅ finnes) og sha256 av drillartefaktet — bytene raden hviler
    på, selv om basen aldri kan lese fila."""
    k = _kjede(migrator)
    did = _drill(migrator, k)
    migrator.execute("RESET ROLE")
    rad = migrator.execute(
        "SELECT tenant, inflight_oppdrag, rullback_oppdrag,"
        " kandidat_oppdrag, artefakt_sha256 FROM moduldrill"
        " WHERE modul_id=%s AND drill_id=%s", (k["mid"], did)).fetchone()
    migrator.rollback()
    assert rad == (k["ten"], k["opp"]["inflight"], k["opp"]["rullback"],
                   k["opp"]["kandidat"], DRILL_SHA)
    # Oppdrag som ikke finnes har ingen utfall — og da finnes ingen rad.
    migrator.execute("RESET ROLE")
    with pytest.raises((psycopg.errors.NoDataFound,
                        psycopg.errors.ForeignKeyViolation)):
        _drill(migrator, k, nokkel="n-" + secrets.token_hex(6),
               opp={**k["opp"], "kandidat": 2 ** 40})
    migrator.rollback()
    migrator.execute("RESET ROLE")


@pg
def test_drillens_egen_epoch_maa_vaere_den_levende(migrator):
    """Codex' P2 (runde 5): `epoch_snapshot` ble snapshottet av basen ved
    REGISTRERINGEN, mens drillartefaktets egen `oppsett.module_epoch`
    aldri ble sendt inn eller sammenlignet.

    Fencing-generasjonen er ikke pynt — den er konteksten claim-stoppet
    ble målt i, og en nødstopp eller reaktivering mellom drill og aksept
    flytter den. Raden kunne derfor påstå en annen generasjon enn
    artefaktet som målte drillen, og SKJULE et misdannet bevis i stedet
    for å avvise det."""
    k = _kjede(migrator)
    # Artefaktet sier én generasjon, registeret står i en annen.
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _drill(migrator, k, epoch=7)
    migrator.rollback()
    assert "epoch" in str(ei.value)
    # Den levende generasjonen går gjennom, og snapshottet ER den.
    migrator.execute("RESET ROLE")
    did = _drill(migrator, k, epoch=0)
    migrator.execute("RESET ROLE")
    snap = migrator.execute(
        "SELECT epoch_snapshot FROM moduldrill WHERE modul_id=%s"
        " AND drill_id=%s", (k["mid"], did)).fetchone()[0]
    migrator.rollback()
    assert snap == 0


@pg
def test_e2e_beviset_maa_komme_av_drillens_kandidatoppdrag(migrator):
    """Codex' P1 (runde 5), A2-leddet: FK-en binder tenant, modul,
    release og promotert tilstand — men ikke HVILKET arbeid artefaktet
    kom av. Et hvilket som helst annet promotert artefakt fra
    kandidatreleasen passerte den, og kontrollen fantes bare i skriptet.
    Nå måler funksjonen båndet selv."""
    k = _kjede(migrator)
    # Et helt gyldig, promotert artefakt på kandidatreleasen — men fra et
    # oppdrag drillen aldri så.
    fremmed = k["artefakt"]("r-kandidat", "promotert")
    migrator.commit()
    did = _drill(migrator, k)
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, k, did, artefakt=fremmed)
    migrator.rollback()
    assert "kandidatoppdrag" in str(ei.value)
    # …og drillens eget bevis går gjennom.
    migrator.execute("RESET ROLE")
    _aksepter(migrator, k, did)
    migrator.commit()
    migrator.execute("RESET ROLE")
    n = migrator.execute("SELECT count(*) FROM modulaksept WHERE modul_id=%s",
                         (k["mid"],)).fetchone()[0]
    migrator.rollback()
    assert n == 1


def test_maalte_bytes_er_drillede_bytes_er_aksepterte_bytes():
    """Codex' P1 (runde 3): de to artefaktene ble validert hver for seg,
    og digestporten i `registrer_moduldrill` sammenlignet bare de to
    DATABASE-releasene med hverandre. Ingenting bandt imaget WCAG-runden
    faktisk MÅLTE til drillens digest, så en deploy med helt andre bytes
    kunne få immutabel aksept på en gammel måling."""
    m = _aksept_skript()
    art = ROT / "deploy/staging/artefakter"
    runde = json.loads((art / "wcag-kontroll-v1-20260818T200413.json"
                        ).read_text(encoding="utf-8"))
    drill = json.loads((art / "rollback-m56-v1-20260820T132200.json"
                        ).read_text(encoding="utf-8"))
    digest = m.verifiser_digestkjede(runde, drill)
    assert digest == drill["oppsett"]["kandidat_digest"]
    # `sha256:`-prefikset er notasjon, ikke en annen digest.
    assert m.verifiser_digestkjede(
        dict(runde, oppsett=dict(runde["oppsett"],
                                 image_digest="sha256:" + digest)),
        drill) == digest
    # Drillet OG kandidat på andre bytes enn de målte: begge skal høres.
    for felt in ("drillet_digest", "kandidat_digest"):
        with pytest.raises(SystemExit) as ei:
            m.verifiser_digestkjede(
                runde, dict(drill, oppsett=dict(drill["oppsett"],
                                                **{felt: "c" * 64})))
        assert "andre bytes" in str(ei.value)
    with pytest.raises(SystemExit):
        m.verifiser_digestkjede(
            dict(runde, oppsett=dict(runde["oppsett"], image_digest="")),
            drill)


@pg
def test_registeret_maa_baere_den_maalte_digesten(migrator):
    """…og den levende sannheten måles med: to innsjekkede artefakter kan
    være enige med hverandre om et image ingen deploymentrad bærer."""
    m = _aksept_skript()
    k = _kjede(migrator)
    migrator.execute("RESET ROLE")
    try:
        m.MODUL = k["mid"]
        m.verifiser_registrert_digest(migrator, ("r-drillet", "r-kandidat"),
                                      "digest-x")
        with pytest.raises(SystemExit) as ei:
            m.verifiser_registrert_digest(
                migrator, ("r-drillet", "finnes-ikke"), "digest-x")
        assert "finnes ikke" in str(ei.value)
        with pytest.raises(SystemExit) as ei:
            m.verifiser_registrert_digest(migrator, ("r-drillet",),
                                          "digest-y")
        assert "andre bytes enn" in str(ei.value)
    finally:
        m.MODUL = "m_wcag_audit"
        migrator.rollback()


def test_akseptskriptet_leser_maaletiden_av_artefaktet():
    """Måletiden skriptet sender, er drillartefaktets `ts` — lest med
    tidssone, aldri klokka i akseptøyeblikket."""
    m = _aksept_skript()
    drill = json.loads((ROT / ("deploy/staging/artefakter/"
                               "rollback-m56-v1-20260820T132200.json")
                        ).read_text(encoding="utf-8"))
    ts = m.drillens_maaletid(drill)
    assert ts.tzinfo is not None and ts.isoformat() == drill["ts"]
    for daarlig, ord_i_feil in (
            ("i går", "ISO-8601"),
            ("2026-08-20T13:22:06", "tidssone"),
            ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
             "fram i tid")):
        with pytest.raises(SystemExit) as ei:
            m.drillens_maaletid(dict(drill, ts=daarlig))
        assert ord_i_feil in str(ei.value), daarlig


def _aksept_skript():
    """Akseptskriptet lastet som modul (filnavnet har bindestrek)."""
    import importlib.util
    sti = ROT / "deploy/staging/m56-aksept.py"
    spec = importlib.util.spec_from_file_location("m56_aksept", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _drillskript():
    """Drillskriptet lastet som modul (filnavnet har bindestrek)."""
    import importlib.util
    sti = ROT / "deploy/staging/rollback-m56.py"
    spec = importlib.util.spec_from_file_location("rollback_m56", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Livslop:
    """Bare det `_deployment` spør om: livsløp per release, eller ingen rad."""

    def __init__(self, **livslop):
        self.livslop, self._svar = livslop, None

    def execute(self, _sql, params=None):
        self._svar = self.livslop.get(params[2]) if params else None
        return self

    def fetchone(self):
        return (self._svar,) if self._svar is not None else None


def test_drillen_nekter_a_gjenoppta_en_avbrutt_kjoring():
    """Codex' P2 (runde 5): docstringen lovet trygg rekjøring, og det
    holdt bare fram til første `bytt_release`.

    Etter rullingen er en ANNEN release claiming, og et nytt forsøk med
    de samme argumentene leser den som «den drillede»: er rullbakken
    claiming blir `drillet == rullback` (CHECK-en i 049 avviser raden —
    etter at hele drillen er kjørt om igjen), er kandidaten claiming
    prøver forsøket å rulle til en release drillen alt drenerte. Begge
    tilstandene etterlates av en unit som ikke starter.

    En drill er ÉN måling og kan ikke gjenopptas — den kan bare kjøres på
    nytt, hel, med ubrukte id-er. Porten sier det FØR noe bestilles."""
    d = _drillskript()
    tom = _Livslop()
    # Det som faktisk er trygt: forsøket døde før første `bytt_release`,
    # ingen av id-ene har en deployment. Da er rekjøring uendret lov.
    d.krev_ubrukte_drillreleaser(tom, "r-drillet", "r-rull", "r-kand")
    # Rullbakken ble claiming — forsøket kom forbi rullingen.
    with pytest.raises(SystemExit) as ei:
        d.krev_ubrukte_drillreleaser(tom, "r-rull", "r-rull", "r-kand")
    assert "ny drill" in str(ei.value).lower()
    # Kandidaten ble claiming — forsøket kom helt fram og døde etterpå.
    with pytest.raises(SystemExit):
        d.krev_ubrukte_drillreleaser(tom, "r-kand", "r-rull", "r-kand")
    # …og en drill-id som alt har en (drenert) deployment er brukt opp.
    for brukt in ({"r-rull": "draining"}, {"r-kand": "retired"}):
        with pytest.raises(SystemExit) as ei:
            d.krev_ubrukte_drillreleaser(_Livslop(**brukt), "r-drillet",
                                         "r-rull", "r-kand")
        assert "brukt opp" in str(ei.value)
    # Løftet i docstringen skal være det porten faktisk holder.
    tekst = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    assert "Rekjøring er trygg" not in tekst


def test_drillartefaktet_maa_navngi_sin_fencing_generasjon():
    """Samme P2, skriptsiden: `oppsett.module_epoch` ble aldri LEST — den
    sto i artefaktet og gikk ingen steder. Nå plukkes den opp, og en
    verdi som ikke er en generasjon høres her, ikke som en typefeil i
    psycopg."""
    m = _aksept_skript()
    ekte = _superseder_drill()
    assert m.drillens_epoch(ekte) == ekte["oppsett"]["module_epoch"]
    for daarlig in (None, "3", -1, True, 1.0):
        with pytest.raises(SystemExit) as ei:
            m.drillens_epoch({"oppsett": {"module_epoch": daarlig}})
        assert "module_epoch" in str(ei.value)


def test_hvert_grensepunkt_har_en_kilde_som_maaler_nettopp_det():
    """Codex' P1 (runde 5): `egress.proxytoken_til_ikke_ekstern_lesing`
    hentet verdien sin fra `maalt.egress_lekkasjer`.

    Det tallet er `port24_motormiljo` — porten som måler om DISPONIT_KEK
    og DATABASE_URL lekker inn i BROWSER-CONTAINERENS miljø. Den ber aldri
    om et proxytoken, for noen modul, av noen klasse. Var
    proxytoken-autorisasjonen brutt for enhver ikke-`ekstern_lesing`
    modul, sto punktet fortsatt «0» i en immutabel akseptrad, fordi
    containermiljøet tilfeldigvis var rent.

    To ting måles her: at kartet punkt→kilde er FULLSTENDIG og DISJUNKT
    mot kravpunktregisteret (et nytt punkt kan ikke stille arve nærmeste
    tall), og at et punkt uten ekte kilde BLOKKERER aksepten i stedet for
    å bli pyntet."""
    m = _aksept_skript()
    punkter = set(re.findall(r"\('wcag-kontroll-v1',\s*'([^']+)'\)",
                             M049.read_text(encoding="utf-8")))
    assert len(punkter) == 21, punkter
    kilder = (set(m.MAALTE), set(m.CI_PUNKTER), set(m.UMAALTE),
              {"malautorisasjon.positiv_sti_virker"})
    flat = [p for s in kilder for p in s]
    assert len(flat) == len(set(flat)), "et punkt har to kilder"
    assert set(flat) == punkter, (
        f"kartet dekker ikke kravpunktregisteret: "
        f"{punkter ^ set(flat)}")
    # Selve feilen: det ene punktet skal ikke lenger hvile på
    # containermiljø-tallet.
    assert "egress.proxytoken_til_ikke_ekstern_lesing" not in m.MAALTE
    skript = (ROT / "deploy/staging/m56-aksept.py").read_text(
        encoding="utf-8")
    assert 'm["egress_lekkasjer"]' not in skript, \
        "punktet henter fortsatt containermiljø-tallet"
    # …og så lenge det står umålt, skrives ingen aksept.
    assert "egress.proxytoken_til_ikke_ekstern_lesing" in m.UMAALTE
    with pytest.raises(SystemExit) as ei:
        m.krev_maalbare_punkter()
    assert "proxytoken" in str(ei.value)


def test_akseptporten_avviser_artefakter_som_ikke_er_bevis():
    """Codex' P1 på PR #117: skriptet leste `bestatt` — kallerens EGEN
    påstand — og skrev deretter en immutabel grønn drill- og akseptrad.
    Porten skal måle alle fire lagene: manifestbindingen, sha256 av de
    leste bytene, det lukkede skjemaet og grensene."""
    import yaml
    m = _aksept_skript()
    man = yaml.safe_load(
        (ROT / "platform/modules/m56_wcag_audit/manifest.yaml").read_text(
            encoding="utf-8"))
    drill_sti = ROT / ("deploy/staging/artefakter/"
                       "rollback-m56-v1-20260820T132200.json")
    runde_sti = ROT / ("deploy/staging/artefakter/"
                       "wcag-kontroll-v1-20260818T200413.json")
    # Det ekte artefaktet passerer — porten er ikke bare streng, den er
    # riktig.
    runde, runde_sha = m.les_bundet_artefakt(runde_sti, KRAV, man)
    assert runde_sha == hashlib.sha256(runde_sti.read_bytes()).hexdigest()
    assert m.verifiser_kilde(runde) == runde["oppsett"]["kilde_sha256"]
    # …og drillen fra 13:22 er ikke lenger bundet (blokkert punkt, Codex
    # P1 runde 3): den stopper på FØRSTE lag, manifestbindingen, akkurat
    # som en fremmed fil ville gjort.
    with pytest.raises(SystemExit) as ei:
        m.les_bundet_artefakt(drill_sti, "rollback-m56-v1", man)
    assert "ikke artefaktet manifestet binder" in str(ei.value)


def test_fabrikkert_artefakt_naar_ikke_de_priviligerte_funksjonene(tmp_path):
    """Selve angrepet Codex beskrev: en håndskrevet JSON-fil med
    `bestatt: true` og passende tellere. Den er ikke manifestbundet, og
    stopper på første lag — før transaksjonen i det hele tatt åpnes."""
    import yaml
    m = _aksept_skript()
    man = yaml.safe_load(
        (ROT / "platform/modules/m56_wcag_audit/manifest.yaml").read_text(
            encoding="utf-8"))
    falsk = tmp_path / "rollback-m56-v1-falsk.json"
    falsk.write_text(json.dumps({
        "krav_id": "rollback-m56-v1", "bestatt": True,
        "maalt": {"nye_oppdrag_claimet_av_drillet_release": 0,
                  "falske_verdikter": 0, "kandidat_promoterte_artefakter": 1},
        "oppsett": {"drillet_release": "wcag-r11",
                    "rullback_release": "wcag-r12",
                    "kandidat_release": "wcag-r13"}}), encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        m.les_bundet_artefakt(falsk, "rollback-m56-v1", man)
    assert "utenfor repoet" in str(ei.value)


def test_akseptporten_maaler_hash_og_grenser(tmp_path):
    """De to lagene bak stikontrollen: en manifestbinding med feil sha
    stopper en lokalt endret fil, og grensene kjøres faktisk — et
    artefakt for ET ANNET krav passerer ikke fordi filnavnet stemte."""
    import yaml
    m = _aksept_skript()
    man = yaml.safe_load(
        (ROT / "platform/modules/m56_wcag_audit/manifest.yaml").read_text(
            encoding="utf-8"))
    rel = "deploy/staging/artefakter/rollback-m56-v1-20260820T132200.json"
    sti = ROT / rel
    feil_sha = dict(man, staging_sjekkliste={"x": {
        "status": "ja", "krav_id": "rollback-m56-v1", "artefakt": rel,
        "artefakt_sha256": SHA0}})
    with pytest.raises(SystemExit) as ei:
        m.les_bundet_artefakt(sti, "rollback-m56-v1", feil_sha)
    assert "endret" in str(ei.value)
    # Riktig sha, feil innhold for kravet: grensene må fyre.
    ekte = hashlib.sha256(sti.read_bytes()).hexdigest()
    forbyttet = dict(man, staging_sjekkliste={"x": {
        "status": "ja", "krav_id": KRAV, "artefakt": rel,
        "artefakt_sha256": ekte}})
    with pytest.raises(SystemExit) as ei:
        m.les_bundet_artefakt(sti, KRAV, forbyttet)
    assert "evidensporten" in str(ei.value)


def test_akseptporten_binder_raafilen():
    """Sammendraget som binder en råfil det ikke er avledet av, er en
    peker til noe som ikke finnes — siste ledd i SP-11-kjeden."""
    m = _aksept_skript()
    art = json.loads((ROT / ("deploy/staging/artefakter/"
                             "wcag-kontroll-v1-20260818T200413.json")
                      ).read_text(encoding="utf-8"))
    mutert = dict(art, oppsett=dict(art["oppsett"], kilde_sha256=SHA0))
    with pytest.raises(SystemExit) as ei:
        m.verifiser_kilde(mutert)
    assert "råfilen" in str(ei.value)
    utenfor = dict(art, oppsett=dict(art["oppsett"], kilde="../../etc/passwd"))
    with pytest.raises(SystemExit) as ei:
        m.verifiser_kilde(utenfor)
    assert "utenfor repoet" in str(ei.value)


def test_artefaktene_maa_navngi_modulen_som_aksepteres():
    """Codex' P2 (runde 3): sammendraget kalte modulen `m56_wcag_audit`
    — KATALOGNAVNET — mens registeret, drillen og 049-radene bruker
    `m_wcag_audit`. Skjemaet krevde bare en ikke-tom streng, og skriptet
    sammenlignet aldri feltet, så evidens for en annen modul kunne bære
    en immutabel `m_wcag_audit`-aksept."""
    from manifestskjema import valider_artefaktformat
    m = _aksept_skript()
    assert m.MODUL == "m_wcag_audit"
    for navn in ("wcag-kontroll-v1-20260818T200413.json",
                 "rollback-m56-v1-20260820T132200.json"):
        art = json.loads((ROT / "deploy/staging/artefakter" / navn
                          ).read_text(encoding="utf-8"))
        assert art["oppsett"]["modul"] == m.MODUL, navn
        m.verifiser_modul(art, navn)                      # ingen SystemExit
        feil = dict(art, oppsett=dict(art["oppsett"], modul="m56_wcag_audit"))
        with pytest.raises(SystemExit) as ei:
            m.verifiser_modul(feil, navn)
        assert "m56_wcag_audit" in str(ei.value)
    # …og skjemaet bærer det samme kravet, uavhengig av skriptet.
    runde = json.loads((ROT / ("deploy/staging/artefakter/"
                               "wcag-kontroll-v1-20260818T200413.json")
                        ).read_text(encoding="utf-8"))
    assert valider_artefaktformat(
        dict(runde, oppsett=dict(runde["oppsett"], modul="m56_wcag_audit")),
        KRAV), "skjemaet godtar fortsatt katalognavnet som modulidentitet"


def test_drillen_maa_vaere_kjort_i_miljoet_som_aksepteres():
    """Codex' P1 (runde 4): modulidentiteten var bundet, miljøet ikke.

    Skjemaet godtar hvilken som helst `oppsett.miljo`, og begge
    basekallene skriver `staging` ubetinget. Release-id-er og digester er
    globale, så et drillartefakt fra produksjon — for de samme releasene
    — passerte både live-tilstands- og digestkontrollen og ble evidens
    for en immutabel STAGING-aksept.
    """
    from manifestskjema import valider_artefaktformat
    m = _aksept_skript()
    assert m.MILJO == "staging"
    drill = _drillartefakt()
    assert drill["oppsett"]["miljo"] == m.MILJO
    m.verifiser_miljo(drill)                              # ingen SystemExit
    for fremmed in ("prod", "dev", ""):
        feil = dict(drill, oppsett=dict(drill["oppsett"], miljo=fremmed))
        with pytest.raises(SystemExit) as ei:
            m.verifiser_miljo(feil)
        assert "miljø" in str(ei.value), fremmed
    # …og et drillartefakt uten miljø i det hele tatt er ikke «staging».
    uten = dict(drill, oppsett={k: v for k, v in drill["oppsett"].items()
                                if k != "miljo"})
    with pytest.raises(SystemExit):
        m.verifiser_miljo(uten)
    # Skjemaet fanger det IKKE — og skal ikke: `rollback-m56-v1` er
    # drillformen, ikke staging-formen. Derfor må skriptet måle det.
    assert not valider_artefaktformat(
        dict(drill, oppsett=dict(drill["oppsett"], miljo="prod")),
        "rollback-m56-v1")


def test_akseptcommiten_baerer_bytene_som_ble_validert(tmp_path):
    """Codex' P1 (runde 2): hash-, skjema- og grensekontrollene hadde
    ARBEIDSTREET som tillitsrot, mens `manifest_commit` var en
    ukontrollert streng eller bare `HEAD`. En commit som ikke finnes,
    og en fil hvis bytes ikke er commitens, skal begge stoppe FØR
    transaksjonen — ellers peker den immutable raden på en commit uten
    ett eneste av bevisene."""
    m = _aksept_skript()
    with pytest.raises(SystemExit) as ei:
        m.loes_akseptcommit("finnes-ikke-i-dette-repoet")
    assert "ingen commit" in str(ei.value)
    hode = m.loes_akseptcommit(None)
    assert len(hode) == 40
    # Manifestet slik det står i HEAD er bundet; en byte til er ikke.
    man_sha = m.les_manifest()[1]
    r = subprocess.run(["git", "-C", str(ROT), "cat-file", "blob",
                        f"{hode}:{m.MANIFEST_REL}"], capture_output=True)
    if r.returncode == 0 and hashlib.sha256(r.stdout).hexdigest() == man_sha:
        m.bind_til_commit(hode, m.MANIFEST_REL, man_sha)   # ingen SystemExit
    with pytest.raises(SystemExit) as ei:
        m.bind_til_commit(hode, m.MANIFEST_REL, SHA0)
    assert "arbeidstreet" in str(ei.value)
    with pytest.raises(SystemExit) as ei:
        m.bind_til_commit(hode, "deploy/staging/finnes-ikke.json", SHA0)
    assert "finnes ikke i" in str(ei.value)


@pg
def test_kvitteringen_leses_uten_admin_fullmakten(migrator):
    """Codex' P1 (runde 2): akseptskriptet leste kvitteringsraden mens
    `SET ROLE disponit_modules_admin` fortsatt sto. 049 gir den rollen
    BARE `EXECUTE` på de to definerne — `SELECT` på tabellen har eier og
    runtime. Aksepten ble altså skrevet og committet, hvorpå lesningen
    ga `permission denied`: kjøringen og hvert forsøk på nytt rapporterte
    feil på en aksept som alt lå der."""
    migrator.execute("SET ROLE disponit_modules_admin")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute("SELECT akseptert_ts FROM modulaksept LIMIT 1")
    migrator.rollback()
    migrator.execute("RESET ROLE")
    migrator.execute("SELECT count(*) FROM modulaksept")   # som migrator: ok
    migrator.rollback()
    # …og skriptet legger ned fullmakten før det leser kvitteringen.
    kilde = (ROT / "deploy/staging/m56-aksept.py").read_text(encoding="utf-8")
    assert kilde.index('conn.execute("RESET ROLE")') \
        < kilde.index("SELECT akseptert_ts"), \
        "kvitteringen leses fortsatt med admin-rollen stående"


def test_invariantpunktene_krever_en_groenn_kjoring_paa_akseptcommiten():
    """Codex' P1 (runde 2): de 16 invariantpunktene ble hardkodet grønne
    fra to strenger kalleren skrev. En kjøring som ikke er ferdig, som
    er RØD, eller som testet en annen commit, skal ikke bære ett eneste
    punkt — og et run-id som ikke er et run-id skal aldri nå nettet."""
    m = _aksept_skript()
    sha = "a" * 40
    groenn = {"id": 42, "status": "completed", "conclusion": "success",
              "head_sha": sha, "path": ".github/workflows/ci.yml"}
    assert m._vurder_ci_kjoring(groenn, "42", sha) == []
    # Codex' P1 (runde 3): en GRØNN kjøring av en annen workflow på samme
    # commit bar alle 16 punktene. `claude.yml` kjører ingen av
    # invarianttestene punktene påberoper seg.
    assert (ROT / ".github/workflows/claude.yml").exists(), \
        "porten under måler nettopp at denne workflowen ikke bærer punktene"
    for muteres, ord_i_feil in (
            ({"conclusion": "failure"}, "conclusion"),
            ({"conclusion": None, "status": "in_progress"}, "ikke ferdig"),
            ({"head_sha": "b" * 40}, "akseptcommiten"),
            ({"path": ".github/workflows/claude.yml"}, "claude.yml"),
            ({"path": None}, "ci.yml"),
            ({"id": 43}, "svarte med kjøring")):
        feil = m._vurder_ci_kjoring(dict(groenn, **muteres), "42", sha)
        assert any(ord_i_feil in f for f in feil), (muteres, feil)
    with pytest.raises(SystemExit) as ei:
        m.verifiser_ci_kjoring("ikke-et-run-id", sha)
    assert "workflow-run-id" in str(ei.value)


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
