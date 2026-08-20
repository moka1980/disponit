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
import atexit
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
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
#: Drillens TIDSLINJE (Codex P1, #117 runde 16). Utfallene måles ikke
#: lenger som ren eksistens av arbeid per release, men mot de to
#: `releasebytte`-overgangene drillen består av. Fixturet bygger derfor
#: den ekte rekkefølgen: inflight bestilt før rullingen og terminalt
#: etter den, rullbakkens oppdrag bestilt etter rullingen og ferdig før
#: kandidaten overtar, kandidatens oppdrag bestilt etter kandidatbyttet.
T_INFLIGHT_BESTILT = DRILL_TS - timedelta(minutes=10)
T_RULL = DRILL_TS - timedelta(minutes=9)
T_INFLIGHT_SLUTT = DRILL_TS - timedelta(minutes=8)
T_RB_BESTILT = DRILL_TS - timedelta(minutes=7)
T_RB_SLUTT = DRILL_TS - timedelta(minutes=6)
T_KAND_BYTTE = DRILL_TS - timedelta(minutes=5)
T_KAND_BESTILT = DRILL_TS - timedelta(minutes=4)
T_KAND_SLUTT = DRILL_TS - timedelta(minutes=3)
#: E2E-artefaktet kandidaten promoterte i drillen — identiteten aksepten
#: skal binde, ikke bare «ett promotert artefakt fra samme release».
E2E_UUID = "ad1579e2-0000-4000-8000-000000000000"
#: sha256 av drillartefaktets bytes — raden skal NAVNGI bevisfilen den
#: hviler på, selv om basen aldri kan lese den (Codex P1, runde 5).
DRILL_SHA = "11" * 32
#: Akseptcommiten CI-kjøringen testet. 40 hex, som en ekte commit-sha:
#: `ci_kjoringsattest.hode_sha` krever formen, for en attest som ikke
#: navngir bytene binder ingenting (Codex P1, runde 16).
CI_SHA = "cc" * 20


def _rt():
    from db.pg import koble
    return koble(DSN)


def _kjede(m, *, promoter_paa_drillet=False, staged_paa_kandidat=False,
           drenerer="par"):
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

    # `signatur` styrer forholdet mellom konvolutten og signaturkolonnen —
    # formene Codex' P1 (runde 8) peker på. True er den ekte (kolonnen ER
    # konvoluttens signaturverdi); False lar kolonnen stå tom ved siden av
    # en fullverdig konvolutt; en streng skrives rått i kolonnen mens
    # konvolutten beholder sin egen. Alle tre må settes ved INSERT: den som
    # skriver dem har direkte `UPDATE`, men `oppdrag_kolonnelaas` fryser
    # kvitteringsfeltene så snart nyttelasten først er lagret, så en
    # forfalskning fødes som rad — den endrer ikke en ekte.
    #
    # `kapabilitet` styrer AVTRYKKET verifiseringsveien etterlater — formen
    # Codex' P1 (runde 15) peker på. De tre kvitteringsfeltene eies av den
    # samme skriveren (kjøretidsrollen har direkte `UPDATE`), så enighet
    # mellom dem er ingen verifisering. Den ekte veien brenner
    # kvitteringskapabiliteten med oppdragets `resultathash` FØR den
    # skriver kvitteringen, og `kvitteringskapabiliteter` kan ingen rolle
    # skrive direkte. True = brent slik veien gjør det; False = ingen
    # kapabilitet (en forfalskning skrevet rett på raden); "utstedt" =
    # utstedt, men aldri brent; "annen_hash" = brent på et annet resultat.
    # `opprettet`/`status_ts` bærer drillens tidslinje (Codex P1, runde
    # 16). Standarden er INFLIGHT-sporet: de negative prøvene bytter ut
    # nettopp inflight-oppdraget, og skal falle på det de måler — ikke på
    # et tidsstempel de ikke handler om.
    def oppdrag(*, status="utfort", kvittering=True, signatur=True,
                eier=None, kapabilitet=True,
                opprettet=T_INFLIGHT_BESTILT, status_ts=T_INFLIGHT_SLUTT):
        sig = secrets.token_hex(16)
        # Samme form som API-veien lagrer: konvolutten står i `kvittering`,
        # signaturverdien ALENE i `kvittering_signatur`, resultathashen i
        # `resultathash` — alle tre i samme UPDATE.
        konvolutt = json.dumps({
            "resultat": "utfort" if status == "utfort" else "feilet",
            "signatur": {"nokkel_id": "k1", "verdi": sig}})
        kolonne = sig if signatur is True else (
            None if signatur is False else signatur)
        blid = m.execute(
            "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
            " beslutning, begrunnelse, idempotency_key, kilde) VALUES"
            " (%s,'h','p','TILLAT','[]'::jsonb,%s,'arbeidskapabilitet')"
            " RETURNING id", (ten, secrets.token_hex(8))).fetchone()[0]
        oid = m.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            " handling, eiermodul, status, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, koblingsstatus,"
            " beslutning_loggpost_id, kvittering, kvittering_signatur,"
            " resultathash, opprettet, status_ts) VALUES ('beslutning',%s,"
            "'kontroll.wcag.nettsted','kontroll.wcag.nettsted',%s,%s,"
            "%s,'k1',%s, now()+interval '1 hour', now()+interval '2 hours',"
            "'KOBLET',%s,%s::jsonb,%s,%s,%s,%s) RETURNING id",
            (ten, eier or mid, status, b"\x00" * 24, b"\x00" * 12, blid,
             konvolutt if kvittering else None,
             kolonne if kvittering else None,
             SHA0 if kvittering else None, opprettet,
             status_ts)).fetchone()[0]
        if kvittering and kapabilitet is not False:
            kap_status = ("utstedt" if kapabilitet == "utstedt" else "brukt")
            kap_hash = (None if kapabilitet == "utstedt"
                        else ("ff" * 32 if kapabilitet == "annen_hash"
                              else SHA0))
            # Tabellen eies av `disponit_m37_claimer` og står `REVOKE ALL
            # ... FROM PUBLIC` — migrator har ingen rettigheter på den.
            # Det er hele poenget med målingen: ingen skriver den
            # direkte. Fixturet må derfor låne eierrollen for å bygge
            # formen den ekte veien etterlater.
            m.execute("SET LOCAL ROLE disponit_m37_claimer")
            m.execute(
                "INSERT INTO kvitteringskapabiliteter (jti, tenant,"
                " oppdrag_id, modul_id, owner_claim_id, owner_generation,"
                " status, resultathash, utloper) VALUES"
                " (%s,%s,%s,%s,'claim-x',1,%s,%s, now()+interval '2 hours')",
                (secrets.token_hex(16), ten, oid, eier or mid,
                 kap_status, kap_hash))
            m.execute("RESET ROLE")
        return oid

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

    # Drillens to registerovergagner — rullingen og kandidatbyttet. Uten
    # dem har registeret ingen drill å måle oppdragene mot, og alle tre
    # utfallene er FALSE (Codex P1, runde 16).
    #
    # HVER OVERGANG ER ET PAR (Codex P1, runde 17): `bytt_release` skriver
    # `drainet_ved_bytte` for den gamle og `releasebytte` for den nye i
    # SAMME transaksjon, altså med samme `ts` og stigende id. Fixturet
    # bygger den formen — en løs `releasebytte` er ingen overgang.
    for drenert, ny, ts in (("r-drillet", "r-rullback", T_RULL),
                            ("r-rullback", "r-kandidat", T_KAND_BYTTE)):
        # `drenerer` finnes for målingen av nettopp paringen: "annen" lar
        # byttet drenere en HELT annen release (formen der overganger fra
        # en annen slekt lånes inn), "ingen" utelater dreneringen (en løs
        # `releasebytte` uten en overgang bak seg).
        if drenerer != "ingen":
            m.execute(
                "INSERT INTO modulregister_hendelse (modul_id, hendelse,"
                " fra_livslop, til_livslop, release_id, miljo,"
                " kontraktversjon, kontrakt_hash, aktor, ts) VALUES"
                " (%s,'drainet_ved_bytte','claiming','draining',%s,"
                "'staging',1,'kh','test',%s)",
                (mid, drenert if drenerer == "par" else "r-annen-slekt",
                 ts))
        m.execute("INSERT INTO modulregister_hendelse (modul_id, hendelse,"
                  " til_livslop, release_id, miljo, kontraktversjon,"
                  " kontrakt_hash, aktor, ts) VALUES"
                  " (%s,'releasebytte','claiming',%s,'staging',1,'kh',"
                  "'test',%s)", (mid, ny, ts))
    inflight = oppdrag()
    rullback = oppdrag(opprettet=T_RB_BESTILT, status_ts=T_RB_SLUTT)
    kandidat = oppdrag(opprettet=T_KAND_BESTILT, status_ts=T_KAND_SLUTT)
    artefakt("r-drillet", "promotert", inflight)
    artefakt("r-rullback", "promotert", rullback)
    ut = {"mid": mid, "ten": ten, "at": at,
          "opp": {"inflight": inflight, "rullback": rullback,
                  "kandidat": kandidat},
          "oppdrag": oppdrag, "artefakt": artefakt,
          "e2e": artefakt("r-kandidat", "promotert", kandidat)}
    if promoter_paa_drillet:
        # EGET oppdrag: `ett_promotert_per_oppdrag` (016) tillater bare ett
        # promotert artefakt per oppdrag, og kandidatoppdraget har alt sitt.
        ut["e2e_drillet"] = artefakt("r-drillet", "promotert")
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


def _ci_attest(m, ci_run="run-1", *, ci_commit=None, arbeidsflyt=None,
               hendelse="push", gren="main", konklusjon="success"):
    """Referatet fra veien som spurte GitHub (Codex P1, #117 runde 16).

    Aksepten regner invariantpunktene mot kravet i `akseptkrav_ci`, ikke
    mot kallerens egne `p_ci_run`/`p_ci_commit`. Uten en attest som sier
    at kravets workflow kjørte grønt på kravets hendelse og gren, for
    nøyaktig akseptcommiten, skrives ingen aksept.
    """
    if arbeidsflyt is None:
        arbeidsflyt = m.execute(
            "SELECT arbeidsflyt FROM akseptkrav_ci WHERE krav_id=%s",
            (KRAV,)).fetchone()[0]
    m.execute("SET ROLE disponit_modules_admin")
    m.execute("SELECT attester_ci_kjoring(%s,%s,%s,%s,%s,%s,'test')",
              (ci_run, arbeidsflyt, hendelse, gren, konklusjon,
               ci_commit or CI_SHA))
    m.execute("RESET ROLE")


def _punkter(m, krav=KRAV, *, evidens_sha="e-sha", ci_run="run-1",
             ci_commit=None):
    """Punktsettet slik REGISTERET krever det (Codex P1, #117 runde 15).

    Grensen, kildetypen og den grønne verdien er registerets, ikke
    kallerens — funksjonen avviser et kall som sier noe annet. Og
    `kilde_ref` må peke på evidens transaksjonen ser: for `evidensfil`
    hashen aksepten binder, for `ci_kjoring` nøyaktig radens egen
    kjøring og commit.
    """
    rader = m.execute(
        "SELECT punkt, kilde_type, grenseverdi, maalt_krav"
        "  FROM akseptkrav_punkt WHERE krav_id=%s", (krav,)).fetchall()
    ref = {"evidensfil": f"evidens.jsonl@sha256:{evidens_sha}",
           "ci_kjoring": f"run {ci_run} @ {ci_commit or CI_SHA}"}
    return {p: {"grenseverdi": g, "maalt_verdi": mk, "kilde_type": kt,
                "kilde_ref": ref[kt]}
            for p, kt, g, mk in rader}


def _aksepter(m, k, did, *, release="r-kandidat", artefakt=None,
              punkter=None, nokkel=None, miljo="staging",
              evidens_sha="e-sha", ci_run="run-1", attest=True):
    m.execute("RESET ROLE")     # forrige _aksepter kan ha etterlatt admin
    if punkter is None:
        # leses som migrator — admin har ikke SELECT
        punkter = _punkter(m, evidens_sha=evidens_sha, ci_run=ci_run)
    if attest:
        # Den ekte veien attesterer kjøringen rett etter at
        # `verifiser_ci_kjoring` har godtatt den (Codex P1, runde 16).
        _ci_attest(m, ci_run)
    m.execute("SET ROLE disponit_modules_admin")
    m.execute(
        "SELECT aksepter_moduldeployment(%s,%s,%s,%s,%s,%s,%s::uuid,%s,"
        "'m-commit',%s,%s,%s::jsonb,%s,'test')",
        (k["mid"], miljo, release, did, KRAV, k["ten"],
         artefakt or k["e2e"], evidens_sha, ci_run, CI_SHA,
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
    """Port 1: drill_id som ikke finnes → avvist, med drillen navngitt.

    Etter runde 5 slår funksjonen selv opp drillraden (den trenger
    kandidatoppdraget for å binde E2E-beviset), så en ukjent drill høres
    der og ikke først i FK-en. FK-en står bak og måles for seg i
    `test_fk_en_staar_bak_akseptfunksjonens_porter`."""
    k = _kjede(migrator)
    with pytest.raises((psycopg.errors.NoDataFound,
                        psycopg.errors.ForeignKeyViolation)) as ei:
        _aksepter(migrator, k, 999999999)
    migrator.rollback()
    assert "999999999" in str(ei.value)


@pg
def test_fk_en_staar_bak_akseptfunksjonens_porter(migrator):
    """Akseptfunksjonen svarer først — men FK-ene er det som faktisk
    bærer, for ENHVER skrivevei.

    Runde 5 flyttet tre kontroller inn i `aksepter_moduldeployment`
    (ukjent drill, E2E-artefaktet fra drillens kandidatoppdrag), og de
    står nå foran FK-ene i rekkefølgen. Da måler ikke de testene lenger
    at FK-ene finnes. Her skrives raden DIREKTE som migrator, forbi
    funksjonen, så strukturen måles der den bor: ukjent drill, artefakt
    fra feil release, og artefakt som ikke er promotert."""
    k = _kjede(migrator, promoter_paa_drillet=True, staged_paa_kandidat=True)
    did = _drill(migrator, k)
    migrator.execute("RESET ROLE")

    def rad(**endring):
        felt = {"drill_id": did, "release_id": "r-kandidat",
                "artefakt": k["e2e"], **endring}
        migrator.execute(
            "INSERT INTO modulaksept (modul_id, miljo, release_id, drill_id,"
            " krav_id, e2e_tenant, e2e_artefakt_id, evidens_jsonl_sha256,"
            " manifest_commit, ci_run, ci_commit, nokkel, aktor) VALUES"
            " (%s,'staging',%s,%s,%s,%s,%s,'e','m','r','c',%s,'test')",
            (k["mid"], felt["release_id"], felt["drill_id"], KRAV, k["ten"],
             felt["artefakt"], "d-" + secrets.token_hex(6)))

    for endring in ({"drill_id": 999999999},
                    {"artefakt": k["e2e_drillet"]},
                    {"artefakt": k["staged"]}):
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as ei:
            rad(**endring)
        assert "modulaksept" in str(ei.value)
        migrator.rollback()
        migrator.execute("RESET ROLE")
    # Motprøven: den riktige formen går gjennom FK-ene.
    rad()
    migrator.rollback()


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
    feller det.

    Etter runde 5 svarer oppdragsbindingen først: artefaktet fra
    r-drillet kom av et annet oppdrag enn drillens kandidatoppdrag, og
    det måles før INSERT-en når FK-en. Samme dom, ett hakk tidligere —
    og FK-en står bak den for enhver annen skrivevei; den måles for seg i
    `test_fk_en_staar_bak_akseptfunksjonens_porter`."""
    k = _kjede(migrator, promoter_paa_drillet=True)
    did = _drill(migrator, k)
    with pytest.raises((psycopg.errors.ForeignKeyViolation,
                        psycopg.errors.InvalidParameterValue)):
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
def test_punktobservasjonene_maales_ikke_bare_mottas(migrator):
    """Codex' P1 (runde 15): `grenseverdi`, `maalt_verdi` og `kilde_ref`
    var frie strenger.

    Funksjonen kontrollerte at de fire feltene FANTES, aldri hva de sa.
    En kaller med `disponit_modules_admin` kunne derfor skrive 21
    immutable observasjoner med hjemmelagde grenser, måletall og
    kildereferanser og oppfylle A3 uten å ha vært innom `m56-aksept.py`.
    Porten mot nettopp det fantes bare i skriptet, og et skript er ingen
    skranke for den som kaller definern direkte.

    Nå eier registeret grensen, kildetypen og den grønne verdien;
    målingen regnes mot kravet, og `kilde_ref` må peke på evidens
    transaksjonen selv ser."""
    k = _kjede(migrator)
    did = _drill(migrator, k)

    def avvist(i_meldingen, *, type_=None, **endring):
        """Muterer ETT punkt (av den gitte kildetypen) og krever avslag."""
        migrator.execute("RESET ROLE")
        p = _punkter(migrator)
        punkt = sorted(pp for pp, v in p.items()
                       if type_ is None or v["kilde_type"] == type_)[0]
        p[punkt] = dict(p[punkt], **endring)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
            _aksepter(migrator, k, did, punkter=p,
                      nokkel="a-" + secrets.token_hex(6))
        migrator.rollback()
        assert punkt in str(ei.value) and i_meldingen in str(ei.value), \
            str(ei.value)

    # (1) Grensen og kildetypen er registerets.
    avvist("registerets", grenseverdi="999")
    avvist("registerets", kilde_type="registerhendelse")
    # (2) Målingen regnes mot kravet — et punkt som ikke oppfyller det,
    #     skrives ikke i det hele tatt.
    avvist("grønn observasjon", maalt_verdi="17")
    # (3) Kilden må peke på evidens transaksjonen ser.
    avvist("evidensfilen", type_="evidensfil",
           kilde_ref="evidens.jsonl@sha256:juks")
    avvist("CI-kjøringen", type_="ci_kjoring", kilde_ref="run 999 @ ci-sha")
    avvist("CI-kjøringen", type_="ci_kjoring",
           kilde_ref="run run-1 @ annen-sha")

    # Motprøven: registerets eget sett går gjennom, og radene bærer
    # REGISTERETS verdier — ikke kallerens.
    migrator.execute("RESET ROLE")
    _aksepter(migrator, k, did)
    migrator.commit()
    migrator.execute("RESET ROLE")
    avvik = migrator.execute(
        "SELECT count(*) FROM modulaksept_punkt p JOIN akseptkrav_punkt r"
        "    ON r.krav_id = p.krav_id AND r.punkt = p.punkt"
        " WHERE p.modul_id=%s AND (p.grenseverdi IS DISTINCT FROM"
        " r.grenseverdi OR p.maalt_verdi IS DISTINCT FROM r.maalt_krav"
        " OR p.kilde_type IS DISTINCT FROM r.kilde_type)",
        (k["mid"],)).fetchone()[0]
    migrator.rollback()
    assert avvik == 0, "en lagret observasjon bærer noe annet enn kravet"


@pg
def test_ci_punktene_krever_referatet_fra_veien_som_spurte(migrator):
    """Codex' P1 (runde 16): CI-kjøringen ble målt mot kallerens egne
    parametre.

    `kilde_ref` ble sammenlignet med `'run ' || p_ci_run || ' @ ' ||
    p_ci_commit` — to felter fra SAMME kall. En `disponit_modules_admin`-
    kaller kunne velge et løpenummer og en commit fritt, gjenta dem i alle
    invariantpunktene og få en immutabel aksept der ingen kjøring hadde
    funnet sted. Likheten målte formen på en streng, ikke at noe var kjørt.

    Basen kan ikke spørre GitHub. Det den kan kreve, er at veien som
    spurte har skrevet ned HVA SVARET SA — workflow, hendelse, gren,
    konklusjon og hode-sha — og så regne punktene mot kravet i
    `akseptkrav_ci` i stedet for mot kalleren."""
    k = _kjede(migrator)
    did = _drill(migrator, k)

    # Uten attest: punktsettet er registerets og `kilde_ref` navngir
    # kjøringen — nøyaktig formen som holdt før. Nå holder den ikke.
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _aksepter(migrator, k, did, ci_run="run-uattestert", attest=False)
    migrator.rollback()
    assert "ingen attest" in str(ei.value)

    # …og en attest som sier noe ANNET enn kravet, bærer ingenting. Hver
    # akse for seg, så en port som avviser alt ikke består testen.
    for navn, endring in (
            ("en annen workflow",
             {"arbeidsflyt": ".github/workflows/claude.yml"}),
            ("en PR-kjøring", {"hendelse": "pull_request"}),
            ("en annen gren", {"gren": "m56-akseptflipp"}),
            ("en rød kjøring", {"konklusjon": "failure"}),
            ("en annen commit", {"ci_commit": "dd" * 20})):
        migrator.execute("RESET ROLE")
        run = "run-" + secrets.token_hex(4)
        _ci_attest(migrator, run, **endring)
        migrator.commit()
        with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
            _aksepter(migrator, k, did, ci_run=run, attest=False,
                      nokkel="a-" + secrets.token_hex(6))
        migrator.rollback()
        assert "ingen attest" in str(ei.value), navn

    # Motprøven: referatet fra den ekte veien bærer punktene.
    migrator.execute("RESET ROLE")
    _aksepter(migrator, k, did)
    migrator.commit()
    migrator.execute("RESET ROLE")
    assert migrator.execute(
        "SELECT count(*) FROM modulaksept WHERE modul_id=%s",
        (k["mid"],)).fetchone()[0] == 1
    migrator.rollback()

    # Én kjøring har ETT utfall: samme løpenummer med et annet referat er
    # ikke en replay, det er to motstridende referater.
    migrator.execute("SET ROLE disponit_modules_admin")
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        migrator.execute(
            "SELECT attester_ci_kjoring('run-1','.github/workflows/ci.yml',"
            "'push','main','failure',%s,'test')", (CI_SHA,))
    migrator.rollback()
    assert "ett utfall" in str(ei.value)
    migrator.execute("RESET ROLE")

    # …og attesten er et referat, ikke en kladd: den kan ikke endres.
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE ci_kjoringsattest SET konklusjon='success'"
                         " WHERE ci_run='run-1'")
    migrator.rollback()


@pg
def test_kravet_kan_ikke_endres_under_en_skrevet_aksept(migrator):
    """Codex' P2 (runde 17): kravpunktregisteret hadde ingen
    append-only-trigger.

    En senere migrasjon kunne derfor rette `grenseverdi`, `maalt_krav`
    eller `kilde_type` for et `krav_id` som ALT har immutable aksepter
    skrevet mot seg — `modulaksept_punkt` binder bare (krav_id, punkt), så
    FK-en fanger det ikke. Radene ville stått som «akseptert mot
    wcag-kontroll-v1» mens observasjonene deres bærer den forrige
    grensen: et revisjonsspor som forteller noe annet enn kallet som
    lagde det.

    En endret grense er et NYTT krav, ikke en rettelse."""
    k = _kjede(migrator)
    did = _drill(migrator, k)
    _aksepter(migrator, k, did)
    migrator.commit()
    migrator.execute("RESET ROLE")
    for sql in (
            "UPDATE akseptkrav_punkt SET grenseverdi='9' WHERE krav_id=%s",
            "UPDATE akseptkrav_punkt SET kilde_type='artefakt'"
            " WHERE krav_id=%s",
            "DELETE FROM akseptkrav_punkt WHERE krav_id=%s",
            "UPDATE akseptkrav_ci SET gren='m56' WHERE krav_id=%s",
            "DELETE FROM akseptkrav_ci WHERE krav_id=%s"):
        with pytest.raises(psycopg.errors.RaiseException):
            migrator.execute(sql, (KRAV,))
        migrator.rollback()
    # …men et NYTT krav kan registreres: porten er mot å endre historien,
    # ikke mot å skrive en ny versjon.
    nytt = "wcag-kontroll-test-" + secrets.token_hex(3)
    migrator.execute("INSERT INTO akseptkrav_punkt (krav_id, punkt,"
                     " kilde_type, grenseverdi, maalt_krav) VALUES"
                     " (%s,'x','evidensfil','0','0')", (nytt,))
    migrator.rollback()


@pg
def test_ci_attesten_er_ikke_runtimes_a_skrive(migrator):
    """Kjøretidsrollen kan verken attestere en kjøring eller skrive
    referatet direkte. Kunne den det, ville attesten vært like fri som de
    to parametrene den erstatter."""
    rt = _rt()
    try:
        for sql in ("SELECT attester_ci_kjoring('r','w','push','main',"
                    "'success','" + "aa" * 20 + "','a')",
                    "INSERT INTO ci_kjoringsattest (ci_run, arbeidsflyt,"
                    " hendelse, gren, konklusjon, hode_sha, aktor) VALUES"
                    " ('r','w','push','main','success','" + "aa" * 20
                    + "','a')",
                    "UPDATE ci_kjoringsattest SET konklusjon='success'",
                    "DELETE FROM ci_kjoringsattest",
                    "INSERT INTO akseptkrav_ci (krav_id, arbeidsflyt,"
                    " hendelse, gren, konklusjon) VALUES"
                    " ('x','w','push','main','success')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(sql)
            rt.rollback()
    finally:
        rt.close()


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
    tabellene — og etter Codex' P1 (runde 14) heller ikke lesing av
    evidensradene: den sanerte statusvisningen er alt."""
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
        # …og evidensradene er ikke lenger lesbare for forespørselsveien
        # (Codex P1, runde 14): de bærer tenantidentifikatorer,
        # oppdrags-IDer, artefakt-UUIDer, aktører og evidensreferanser.
        for tabell in ("moduldrill", "modulaksept", "modulaksept_punkt"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(f"SELECT count(*) FROM {tabell}")
            rt.rollback()
        # Det statusflaten trenger, står i den sanerte visningen — og
        # kravpunktregisteret er en ren katalog uten tenantdata.
        rt.execute("SELECT count(*) FROM modulaksept_status")
        rt.rollback()
        rt.execute("SELECT count(*) FROM akseptkrav_punkt")
        rt.rollback()
    finally:
        rt.close()


@pg
def test_evidensradene_er_tenantfiltrert(migrator):
    """Codex' P1 (runde 14): `moduldrill` og `modulaksept` sto UTEN RLS
    mens kjøretidsrollen hadde `SELECT` på dem. Nabotabellen `artefakt`
    er tenant-filtrert; disse var det ikke, så én forespørselsvei utenfor
    sin egen tenantkontekst leste hver eneste tenants driftsbevis.

    Fullmakten er trukket tilbake, men porten skal stå på RADEN også: en
    fullmakt kan gis igjen ved et uhell, en policy gjelder uansett hvem
    som får `SELECT` senere. Her måles policyen direkte, med
    admin-rollen som prober — den har ingen tabellfullmakt fra før, så
    den får den midlertidig i transaksjonen og RLS er det eneste som
    står igjen."""
    k = _kjede(migrator)
    drill = _drill(migrator, k)
    migrator.execute("GRANT SELECT ON moduldrill TO disponit_modules_admin")
    migrator.execute("SET ROLE disponit_modules_admin")
    def _synlige():
        return migrator.execute(
            "SELECT count(*) FROM moduldrill WHERE drill_id=%s",
            (drill,)).fetchone()[0]

    try:
        # UTEN tenantkontekst: ingen rader. `_kjede` setter GUC-en på
        # SESJONEN, så den må nullstilles her — ellers måler prøven at
        # raden er synlig i sin egen tenant, som den skal være.
        migrator.execute("SELECT set_config('disponit.tenant','',true)")
        assert _synlige() == 0
        # …og i en ANNEN tenants kontekst er den like usynlig.
        migrator.execute("SELECT set_config('disponit.tenant','en-annen',true)")
        assert _synlige() == 0
        # …men i sin EGEN tenant er raden der.
        migrator.execute("SELECT set_config('disponit.tenant',%s,true)",
                         (k["ten"],))
        assert _synlige() == 1
    finally:
        migrator.rollback()
        migrator.execute("RESET ROLE")


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
    #
    # «Hel» inkluderer de ti `kjoring`-linjene: fase 5-summen er ikke
    # selv en måling (Codex P2, runde 10), og en runde som bare bærer
    # summen har ingen frist- og fasitmålinger å regne den ut av.
    typer = ("fase5_resultat", "port20_robots", "port20_robots_5xx",
             "port21_frekvens", "port24_motormiljo",
             "feilinjisering_motorfeil", "feilinjisering_evidensfrist")
    i_fase5 = max(i for i, d in enumerate(rader)
                  if d["hendelse"] == "fase5_resultat")
    hel = [{"ts": "2026-08-19T09:00:00+00:00", "hendelse": "fase1_ok",
            "image_id": f"sha256:{fremmed}"}]
    hel += [dict(d, ts="2026-08-19T08:59:59+00:00")
            for d in m.fase5_linjene(rader, m.kontekster(rader), i_fase5)]
    for n, navn in enumerate(typer):
        siste = [d for d in rader if d["hendelse"] == navn
                 and d.get("utfall") != "tomt"][-1]
        hel.append(dict(siste, ts=f"2026-08-19T09:00:{n + 1:02d}+00:00"))
    ny = _sammendrag(hel)
    assert ny["oppsett"]["image_digest"] == fremmed
    assert ny["oppsett"]["release"] == \
        [d for d in rader if d["hendelse"] == "fase2_ok"][-1]["release"]
    assert ny["maalt"] == art["maalt"] and ny["bestatt"] is True


def test_fristtellingen_regnes_ut_av_de_ti_kjoringene():
    """Codex' P2 (runde 10): `kjoringer_signert_innen_frist` ble kopiert
    ordrett fra `fase5_resultat`s summeringsstreng.

    Fase 5 er idempotent: er rapporten alt lesbar (`alt_utfort`), settes
    `utfall: "utfort"` uten at motoren kjøres, `varighet_s` blir `None`,
    og linjas `ok` blir grønn UTEN at fristen måles på nytt. Ti rapporter
    som opprinnelig brøt fristen kunne derfor bli «10/10» ved neste
    gjenkjøring — og et sammendrag som bare arver den strengen, bærer
    påstanden videre inn i en immutabel aksept."""
    m = _runde_skript()
    art = json.loads((ROT / ("deploy/staging/artefakter/"
                             "wcag-kontroll-v1-20260818T200413.json")
                      ).read_text(encoding="utf-8"))
    kilde = ROT / art["oppsett"]["kilde"]
    rader = m.les(kilde)
    kontekst = m.kontekster(rader)
    i_fase5 = max(i for i, d in enumerate(rader)
                  if d["hendelse"] == "fase5_resultat")
    linjer = m.fase5_linjene(rader, kontekst, i_fase5)
    # Den innsjekkede runden BLE målt: ti linjer med egen varighet.
    assert len(linjer) == 10
    assert m.rent_innen_frist(linjer, 10) == 10

    # Gjenspill: samme ti rapporter, men ingen frist målt.
    gjenspilt = [dict(d, gjenspill=True, varighet_s=None) for d in linjer]
    assert m.rent_innen_frist(gjenspilt, 10) == 0
    # En kjøring som brøt sin egen frist teller ikke, uansett `ok`.
    over = [dict(d, varighet_s=d["frist_s"] + 1, ok=True) for d in linjer]
    assert m.rent_innen_frist(over, 10) == 0
    # …og avvik mot fasiten er heller ikke et signert, rent utfall.
    avvik = [dict(d, avvik_mot_fasit=1) for d in linjer]
    assert m.rent_innen_frist(avvik, 10) == 0
    # Samme posisjon to ganger er én kjøring, ikke to.
    assert m.rent_innen_frist(linjer + linjer, 10) == 10

    # Codex' P2 (runde 15): en halvskrevet eller fabrikkert linje bærer
    # ingen måling. Et manglende `avvik_mot_fasit` ble null avvik av
    # `int(x or 0)`, og `bool` slapp gjennom talltesten fordi den arver
    # `int` — `varighet_s: False` var «0 sekunder, innenfor fristen».
    assert m.rent_innen_frist(
        [{k: v for k, v in d.items() if k != "avvik_mot_fasit"}
         for d in linjer], 10) == 0
    assert m.rent_innen_frist(
        [dict(d, avvik_mot_fasit=False) for d in linjer], 10) == 0
    assert m.rent_innen_frist(
        [dict(d, varighet_s=False) for d in linjer], 10) == 0
    assert m.rent_innen_frist(
        [dict(d, varighet_s=float("nan")) for d in linjer], 10) == 0
    assert m.rent_innen_frist(
        [dict(d, frist_s=float("inf")) for d in linjer], 10) == 0
    assert m.rent_innen_frist(
        [dict(d, avvik_mot_fasit="0") for d in linjer], 10) == 0
    # …og uten en posisjon å avduplisere på er linja ikke en av de ti.
    assert m.rent_innen_frist(
        [{k: v for k, v in d.items() if k != "i"} for d in linjer], 10) == 0

    # Codex' P2 (runde 13): posisjonen ble bare typesjekket. Ti ellers
    # grønne linjer på posisjoner UTENFOR de bestilte — 10–19, fra en
    # halvskrevet, sammenslått eller redigert evidensfil — ga et sett på
    # ti og bekreftet «10/10» uten at én av de ti BESTILTE kjøringene var
    # målt. Fase 5 bestiller `range(krav)`; alt annet er ikke en av dem.
    forskjovet = [dict(d, i=d["i"] + 10) for d in linjer]
    assert m.rent_innen_frist(forskjovet, 10) == 0
    assert m.rent_innen_frist([dict(d, i=-1) for d in linjer], 10) == 0
    # Ni bestilte + én utenfor er ni målte kjøringer, ikke ti.
    assert m.rent_innen_frist(
        [d for d in linjer if d["i"] != 9] + [dict(linjer[9], i=42)],
        10) == 9
    # Og en generator som ble bestilt færre kjøringer, teller bare dem.
    assert m.rent_innen_frist(linjer, 4) == 4

    # Codex' P2 (runde 14): posisjonen ble avduplikert, men ikke
    # IDENTITETEN bak den. Ett vellykket oppdrag kopiert inn på alle ti
    # bestilte posisjoner ga et sett på ti og dermed «10/10», selv om
    # motoren hadde kjørt én eneste gang.
    ett_oppdrag = [dict(d, oppdrag=linjer[0]["oppdrag"]) for d in linjer]
    assert m.rent_innen_frist(ett_oppdrag, 10) == 1
    # To ekte kjøringer gjentatt over ti posisjoner er fortsatt to.
    to_oppdrag = [dict(d, oppdrag=linjer[d["i"] % 2]["oppdrag"])
                  for d in linjer]
    assert m.rent_innen_frist(to_oppdrag, 10) == 2
    # En linje uten en egen oppdrags-ID har ikke målt en egen kjøring —
    # og en uhashbar eller påstått identitet skal feile lukket, ikke
    # kaste ut av genereringen.
    assert m.rent_innen_frist(
        [{k: v for k, v in d.items() if k != "oppdrag"} for d in linjer],
        10) == 0
    assert m.rent_innen_frist(
        [dict(d, oppdrag={}) for d in linjer], 10) == 0
    assert m.rent_innen_frist(
        [dict(d, oppdrag=True) for d in linjer], 10) == 0
    assert m.rent_innen_frist(
        [dict(d, oppdrag="  ") for d in linjer], 10) == 0

    # Codex' P2 (runde 15): identiteten var TEGNENE (`json.dumps` av hva
    # linja bar), ikke tallet. Da var `12`, `"12"`, `"012"`, `"+12"` og
    # `" 12"` fem oppdrag — og ETT oppdrag kunne igjen fylle alle ti
    # bestilte posisjoner, som avdupliseringen fantes for å hindre.
    #
    # `12` og `"12"` ER samme oppdrag: ti posisjoner, annenhver
    # skrivemåte, én kjøring.
    ett_id = linjer[0]["oppdrag"]
    assert m.rent_innen_frist(
        [dict(d, oppdrag=ett_id if d["i"] % 2 else str(ett_id))
         for d in linjer], 10) == 1
    # …og skrivemåter som ikke kommer fra basen er ingen oppdrags-id:
    # ledende null, fortegn, mellomrom, tomt, null og negativt.
    for ikke_id in (f"0{ett_id}", f"+{ett_id}", f" {ett_id}", f"{ett_id} ",
                    "", "0", -ett_id, f"{ett_id}.0", f"{ett_id}\n"):
        assert m.rent_innen_frist(
            [dict(d, oppdrag=ikke_id) for d in linjer], 10) == 0, ikke_id
    # `true` er fortsatt ikke oppdrag nummer 1 (bool arver int).
    assert m._identitet({"oppdrag": True}, "oppdrag") is None
    assert m._identitet({"oppdrag": 12}, "oppdrag") \
        == m._identitet({"oppdrag": "12"}, "oppdrag") == 12

    # Og generatoren nekter å skrive et sammendrag der summen påstår mer
    # enn linjene bærer: en ren gjenspilt runde stopper her.
    fase5 = rader[i_fase5]
    gjenspilt_runde = (
        rader[:i_fase5 - len(linjer)]
        + [dict(d, gjenspill=True, varighet_s=None) for d in linjer]
        + [dict(fase5, ti_kjoringer_signert_innen_frist="10/10", ok=True)]
        + rader[i_fase5 + 1:])
    with pytest.raises(SystemExit) as ei:
        m.sammendrag(gjenspilt_runde, art["oppsett"]["kilde"],
                     art["oppsett"]["kilde_sha256"])
    assert "gjenspill" in str(ei.value)

    # …og like lite skriver den et sammendrag av ti grønne linjer som
    # aldri sto på en bestilt posisjon (Codex P2, runde 13).
    forskjovet_runde = (
        rader[:i_fase5 - len(linjer)]
        + [dict(d, i=d["i"] + 10) for d in linjer]
        + rader[i_fase5:])
    with pytest.raises(SystemExit) as ei:
        m.sammendrag(forskjovet_runde, art["oppsett"]["kilde"],
                     art["oppsett"]["kilde_sha256"])
    assert "BESTILTE" in str(ei.value)

    # …og heller ikke av ti linjer som alle viser til det SAMME oppdraget
    # (Codex P2, runde 14).
    ett_oppdrag_runde = (
        rader[:i_fase5 - len(linjer)]
        + [dict(d, oppdrag=linjer[0]["oppdrag"]) for d in linjer]
        + rader[i_fase5:])
    with pytest.raises(SystemExit) as ei:
        m.sammendrag(ett_oppdrag_runde, art["oppsett"]["kilde"],
                     art["oppsett"]["kilde_sha256"])
    assert "oppdrags-ID" in str(ei.value)


def test_signert_er_en_egen_maaling_ikke_et_navn_paa_fristtallet():
    """Codex' P2 (runde 15): predikatet het «signert innen frist» og
    inneholdt ikke én måling av en signatur.

    Rent utfall, null avvik, målt varighet under egen frist og en egen
    oppdrags-ID — alt sammen ekte målinger, men ingen av dem sier noe om
    kvitteringen bak rapporten. Ti usignerte eller manglende kvitteringer
    ga et grønt «10/10», og ordet «signert» i navnet var hele beviset.

    Nå er signaturen produsentens egen måling (`kvittering_signert` på
    hver `kjoring`-linje, målt i basen), fristtallet heter det det måler,
    og de to har hvert sitt tall i sammendraget."""
    m = _runde_skript()
    art = json.loads((ROT / ("deploy/staging/artefakter/"
                             "wcag-kontroll-v1-20260818T200413.json")
                      ).read_text(encoding="utf-8"))
    rader = m.les(ROT / art["oppsett"]["kilde"])
    kontekst = m.kontekster(rader)
    i_fase5 = max(i for i, d in enumerate(rader)
                  if d["hendelse"] == "fase5_resultat")
    linjer = m.fase5_linjene(rader, kontekst, i_fase5)

    # Runden fra 18/8 målte fristen på alle ti — og signaturen på ingen.
    assert m.rent_innen_frist(linjer, 10) == 10
    assert m.signert_innen_frist(linjer, 10) == 0
    assert art["maalt"]["kjoringer_rent_innen_frist"] == 10
    assert art["maalt"]["kjoringer_med_maalt_signatur"] == 0

    # Med målingen på plass teller de.
    signerte = [dict(d, kvittering_signert=True) for d in linjer]
    assert m.signert_innen_frist(signerte, 10) == 10
    # `is True`, ikke sannhetsverdi: alt annet er en linje fra en runde
    # som ikke målte dette, og fail-closed er å ikke telle den.
    for ikke_maalt in (False, None, "ja", 1, "true", [], {}):
        assert m.signert_innen_frist(
            [dict(d, kvittering_signert=ikke_maalt) for d in linjer],
            10) == 0, ikke_maalt
    # Signaturen frikjenner ikke en kjøring som brøt fristen sin.
    assert m.signert_innen_frist(
        [dict(d, kvittering_signert=True, varighet_s=d["frist_s"] + 1)
         for d in linjer], 10) == 0
    # …og en delvis signert runde teller bare de signerte.
    delvis = [dict(d, kvittering_signert=d["i"] < 4) for d in linjer]
    assert m.signert_innen_frist(delvis, 10) == 4
    assert m.rent_innen_frist(delvis, 10) == 10

    # Akseptskriptet BLOKKERER så lenge signaturen ikke er målt: punktet
    # heter «signert», og et punkt kan ikke hvile på et tall som ikke er
    # en signaturmåling.
    a = _aksept_skript()
    assert a.MAALTE["kontroll.ti_kjoringer_signert_innen_frist"][1](
        {"kjoringer_med_maalt_signatur": 10, "kjoringer_krav": 10}) == "10/10"
    with pytest.raises(SystemExit) as ei:
        a.krev_maalt_signatur(art["maalt"])
    assert "signert" in str(ei.value)
    a.krev_maalt_signatur({"kjoringer_med_maalt_signatur": 10,
                           "kjoringer_krav": 10})
    # `True == 1` i Python: en bool er ikke en måling av ti kjøringer.
    for falsk in ({"kjoringer_med_maalt_signatur": True, "kjoringer_krav": 1},
                  {"kjoringer_med_maalt_signatur": "10", "kjoringer_krav": 10},
                  {"kjoringer_krav": 10}):
        with pytest.raises(SystemExit):
            a.krev_maalt_signatur(falsk)

    # Og produsenten MÅLER den — den påstår den ikke.
    sjekk = (ROT / "deploy/staging/wcag-staging-sjekkliste.py").read_text(
        encoding="utf-8")
    assert "kvittering_signert=signert" in sjekk
    # …gjennom den DELTE porten (Codex P1, runde 17): predikatet bor i
    # `maal_rent_utfall`, bak fullmakten det trenger — en kopi her ville
    # kjørt som migrator, og `kvitteringskapabiliteter` er
    # `REVOKE ALL … FROM PUBLIC` med kolonnegrant bare til modul_eier.
    assert "maal_rent_utfall" in sjekk, (
        "signaturmålingen krever avtrykket verifiseringsveien setter"
        " igjen — ikke at to felter samme skriver eier er enige")


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
    # Codex' P2 (runde 2): 11 rene av 10 kjørte er ikke et strengere
    # bevis, det er et tall som ikke stemmer med seg selv — og
    # akseptraden ville båret «11/10» som om det var bestått.
    umulig = _mutert(kjoringer_rent_innen_frist=11, kjoringer_krav=10)
    assert any("kjoringer_rent_innen_frist" in f
               for f in _sjekk_grenser(KRAV, umulig)), \
        "flere rene kjøringer enn kjørte slapp gjennom porten"
    # …og det samme gjelder signaturtallet, som er sin egen måling
    # (Codex P2, runde 15): flere målte signaturer enn kjøringer finnes
    # ikke. Under kravet er derimot lovlig HER — det blokkerer i
    # akseptskriptet, ikke i evidensporten, fordi runden fra 18/8 aldri
    # målte signaturen og artefaktet skal kunne si nettopp det.
    umulig_sig = _mutert(kjoringer_med_maalt_signatur=11, kjoringer_krav=10)
    assert any("kjoringer_med_maalt_signatur" in f
               for f in _sjekk_grenser(KRAV, umulig_sig)), \
        "flere målte signaturer enn kjøringer slapp gjennom porten"
    assert not _sjekk_grenser(KRAV, _mutert(kjoringer_med_maalt_signatur=0))
    assert _sjekk_grenser(KRAV, _mutert(kjoringer_rent_innen_frist=9))


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
    # Rullbakken bærer FORGJENGERENS bytes (Codex P1, runde 6): hvem hun
    # var, hvilke bytes hun hadde, og hvilke rullback-releasen faktisk
    # fikk. Porten regner likheten ut av digestene, ikke av flagget.
    forgjenger = ekte["oppsett"]["drillet_digest"]
    return dict(ekte, maalt=komplett, identiteter=ident,
                oppsett=dict(ekte["oppsett"],
                             forgjenger_release="wcag-r10",
                             forgjenger_digest=forgjenger,
                             rullback_digest=forgjenger),
                etterkontroll=dict(ekte["etterkontroll"],
                                   rullback_livslop="draining",
                                   rullback_bytes_er_forgjengerens=True))


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


def test_rullbakken_maa_baere_forgjengerens_bytes():
    """Codex' P1 (runde 6): rullback-releasen ble registrert med den
    DRILLEDE deploymentens digest.

    «Rullbakken» var dermed kandidatens egne bytes under et nytt navn,
    og (b2) kunne stå grønt uten at det man ruller tilbake TIL noen gang
    var prøvd — en forgjenger som ikke lar seg kjøre på verten ville
    fortsatt gitt et grønt rullbakkartefakt. Artefaktet navngir nå
    forgjengeren og bærer begge digestene, og porten regner likheten ut
    av dem — aldri av etterkontrollens flagg alene."""
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    drillkrav = "rollback-m56-v1"
    assert _sjekk_grenser(drillkrav, _drillartefakt()) == []
    # Selve angrepet: rullbakken fikk de DRILLEDE bytene, ikke
    # forgjengerens. Flagget står grønt — porten skal ikke tro på det.
    laant = _drillartefakt()
    laant["oppsett"] = dict(laant["oppsett"],
                            forgjenger_digest="ff" * 32)
    assert any("forgjengerens" in f
               for f in _sjekk_grenser(drillkrav, laant)), \
        "rullbakk med andre bytes enn forgjengerens slapp gjennom"
    # Flagget alene er heller ikke nok den andre veien.
    uten_flagg = _drillartefakt()
    uten_flagg["etterkontroll"] = dict(
        uten_flagg["etterkontroll"], rullback_bytes_er_forgjengerens=False)
    assert any("forgjengerens bytes" in f
               for f in _sjekk_grenser(drillkrav, uten_flagg))
    # En «rullbakk» til seg selv har ingen retning.
    seg_selv = _drillartefakt()
    seg_selv["oppsett"] = dict(
        seg_selv["oppsett"],
        forgjenger_release=seg_selv["oppsett"]["drillet_release"])
    assert any("forgjenger_release" in f
               for f in _sjekk_grenser(drillkrav, seg_selv))
    # …og skjemaet krever feltene, så et artefakt uten dem er ikke bare
    # umålt — det er ugyldig.
    for felt in ("forgjenger_release", "forgjenger_digest",
                 "rullback_digest"):
        mangler = _drillartefakt()
        mangler["oppsett"] = {k: v for k, v in mangler["oppsett"].items()
                              if k != felt}
        assert valider_artefaktformat(mangler, drillkrav), \
            f"skjemaet krever ikke {felt}"


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

    # Codex' P2 (runde 15): domenesjekkene kjører BEVISST videre etter en
    # skjemafeil, så en uhashbar identitet som `{}`/`[]` nådde `set(...)`
    # og valideringen slapp ut med en `TypeError`-traceback i stedet for
    # de akkumulerte feilene. Denne veien skal feile LUKKET.
    for ugyldig in ({}, [], 7, None, "", "   "):
        skakk = dict(drill, identiteter=dict(drill["identiteter"],
                                             kandidat_artefakter=[ugyldig]))
        assert valider_artefaktformat(skakk, drillkrav), \
            f"skjemaet skulle rapportert {ugyldig!r} som formatfeil"
        feil = _sjekk_grenser(drillkrav, skakk)
        assert any("kandidat_artefakter" in f for f in feil), \
            f"{ugyldig!r} ga ingen akkumulert domenefeil: {feil}"


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


def test_datasettpunktet_krever_den_lokale_halvdelen():
    """Codex' P2 (runde 10): `syntetisk_datasett_likt_lokalt` sto `ja` på
    en måling som bare finnes på staging.

    Punktets eneste bevismåling var `maalt.avvik_mot_fasit`, og den
    avleder `wcag-kontroll-artefakt.py` utelukkende av staging-rundens
    `fase5_resultat`. Null avvik der sier at motoren traff fasiten på
    staging; det sier ingenting om at datasettet er det samme lokalt — og
    verken artefaktet eller evidensfila bærer en identitet for bytene
    staging serverte. Halve påstanden var altså umålt, og et `ja` på et
    umålt ledd er nøyaktig den lånte konklusjonen `bevismaalinger` finnes
    for å stoppe."""
    import yaml
    man = yaml.safe_load(
        (ROT / "platform/modules/m56_wcag_audit/manifest.yaml").read_text(
            encoding="utf-8"))
    p = man["staging_sjekkliste"]["syntetisk_datasett_likt_lokalt"]
    assert p["status"] == "blokkert" and p.get("blokkert_av")
    assert "artefakt" not in p and "bevismaalinger" not in p, \
        "et blokkert punkt skal ikke bære en evidensbinding"
    # Begrunnelsen må si hva som faktisk lukker gapet — ellers er
    # `blokkert` bare et penere `nei`.
    assert "lokal" in p["blokkert_av"].lower()
    # Datasettet ER sjekket inn, så den lokale siden lar seg måle når
    # noen kjører runden: stien i begrunnelsen skal finnes.
    assert (ROT / "platform/modules/m56_wcag_audit/testnettsted"
            / "fasit.json").exists()


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
def test_drilloppdragene_maa_ligge_i_det_maalte_rullbakkvinduet(migrator):
    """Codex' P1 (runde 16): utfallene ble målt som ren EKSISTENS av
    arbeid per release.

    Har de tre releasene håndtert vanlig arbeid på noe tidspunkt — og det
    har en release som har vært claiming — kunne et direkte
    `registrer_moduldrill`-kall plukke et signert, fullført oppdrag fra den
    drillede og promoterte oppdrag fra rullbakken og kandidaten, og få alle
    tre flaggene grønne uten at noe kappløp, noe claim-stopp eller noen
    rulling hadde funnet sted. Predikatene sa «det finnes arbeid på denne
    releasen», mens påstanden er «dette arbeidet krysset rullingen».

    `bytt_release` skriver én `releasebytte`-hendelse per overgang, med
    tidspunkt. Oppdragene måles nå mot drillens egne to skillelinjer."""
    k = _kjede(migrator)
    # Positiv kontroll FØRST: den ekte tidslinjen gir grønt på alle tre.
    # Uten den ville resten bestått av en port som avviste alt.
    did = _drill(migrator, k)
    migrator.execute("RESET ROLE")
    assert migrator.execute(
        "SELECT claim_stopp_ok, rene_utfall_ok, tilbake_ok FROM moduldrill"
        " WHERE drill_id=%s", (did,)).fetchone() == (True, True, True)
    migrator.rollback()

    # LÅNT ARBEID: nøyaktig samme artefaktform, men oppdragene ligger
    # utenfor vinduet — inflight bestilt ETTER rullingen (krysset den
    # aldri), rullbakkens bestilt FØR den (kan ikke ha målt et claim-stopp
    # under dreneringen), kandidatens bestilt FØR kandidatbyttet (lå ikke
    # og ventet på den som overtok).
    laant = {
        "inflight": k["oppdrag"](opprettet=T_RULL + timedelta(minutes=1),
                                 status_ts=T_INFLIGHT_SLUTT),
        "rullback": k["oppdrag"](opprettet=T_INFLIGHT_BESTILT,
                                 status_ts=T_RB_SLUTT),
        "kandidat": k["oppdrag"](opprettet=T_KAND_BYTTE - timedelta(minutes=1),
                                 status_ts=T_KAND_SLUTT),
    }
    k["artefakt"]("r-drillet", "promotert", laant["inflight"])
    k["artefakt"]("r-rullback", "promotert", laant["rullback"])
    k["artefakt"]("r-kandidat", "promotert", laant["kandidat"])
    migrator.commit()
    did2 = _drill(migrator, k, nokkel="n-" + secrets.token_hex(6), opp=laant)
    migrator.execute("RESET ROLE")
    assert migrator.execute(
        "SELECT claim_stopp_ok, rene_utfall_ok, tilbake_ok FROM moduldrill"
        " WHERE drill_id=%s", (did2,)).fetchone() == (False, False, False), \
        "lånt arbeid utenfor rullbakkvinduet teller fortsatt som drill"
    migrator.rollback()

    # OVERGANGEN MÅ VÆRE DEN SOM DRENERTE FORGJENGEREN (Codex P1, runde
    # 17). En løs `releasebytte`, eller en som drenerte en HELT annen
    # release, er ingen rullbakk FRA det som ble drillet — og da er det
    # ingen drill å måle oppdragene mot, uansett hvor riktig de ligger.
    for form in ("ingen", "annen"):
        k2 = _kjede(migrator, drenerer=form)
        d2 = _drill(migrator, k2, nokkel="n-" + secrets.token_hex(6))
        migrator.execute("RESET ROLE")
        assert migrator.execute(
            "SELECT claim_stopp_ok, rene_utfall_ok, tilbake_ok"
            "  FROM moduldrill WHERE drill_id=%s",
            (d2,)).fetchone() == (False, False, False), form
        migrator.rollback()

    # …og et vindu som ikke lukkes FØR målingen, er heller ingen drill: en
    # senere overgang inn på kandidaten flytter skillelinjen forbi
    # drillens egen `utfort_ts`, og da er ingenting målt i det vinduet.
    senere = DRILL_TS + timedelta(hours=1)
    migrator.execute(
        "INSERT INTO modulregister_hendelse (modul_id, hendelse,"
        " fra_livslop, til_livslop, release_id, miljo, kontraktversjon,"
        " kontrakt_hash, aktor, ts) VALUES (%s,'drainet_ved_bytte',"
        "'claiming','draining','r-rullback','staging',1,'kh','test',%s)",
        (k["mid"], senere))
    migrator.execute(
        "INSERT INTO modulregister_hendelse (modul_id, hendelse,"
        " til_livslop, release_id, miljo, kontraktversjon, kontrakt_hash,"
        " aktor, ts) VALUES (%s,'releasebytte','claiming','r-kandidat',"
        "'staging',1,'kh','test',%s)", (k["mid"], senere))
    migrator.commit()
    did3 = _drill(migrator, k, nokkel="n-" + secrets.token_hex(6))
    migrator.execute("RESET ROLE")
    assert migrator.execute(
        "SELECT claim_stopp_ok, rene_utfall_ok, tilbake_ok FROM moduldrill"
        " WHERE drill_id=%s", (did3,)).fetchone() == (False, False, False), \
        "drillen måler mot en overgang som ligger etter dens egen måletid"
    migrator.rollback()


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
def test_rent_utfall_krever_signaturen_ikke_bare_nyttelasten(migrator):
    """Codex' P1 (runde 8): «signert kvittering» ble målt på JSON-blobben.

    Porten leste `kvittering IS NOT NULL` og kalte utfallet signert. Men
    signaturen er en EGEN kolonne (`oppdrag.kvittering_signatur`), og
    `oppdrag`-skjemaet lar de to variere fritt: kolonnelåsen fryser
    kvitteringsfeltene etter at de er satt, men krever ikke at en nyttelast
    har en signatur ved siden av seg — og kjøretidsrollen har direkte
    `UPDATE`. Én rad med en håndskrevet konvolutt og tom signaturkolonne ga
    dermed `rene_utfall_ok = true`, og aksepten — uforanderlig når den først
    er skrevet — påsto for alltid at drillen endte i en signert kvittering
    det ikke fantes noen signatur for.

    Tre former må falle, og de skiller seg fra hverandre: signaturen
    mangler, signaturen er tom, og signaturen finnes men er en ANNEN enn
    den konvolutten bærer (da kommer raden ikke fra veien som verifiserte).
    """
    k = _kjede(migrator)

    def maal(signatur):
        """Bygger en inflight-rad med den gitte signaturformen og måler.

        Kolonnelåsen fryser kvitteringsfeltene så snart nyttelasten er
        lagret, så hver form fødes som sin EGEN rad. Det er også den
        realistiske angrepsformen: en fabrikkert rad, ikke en endret ekte.
        Alt annet — status, promotert artefakt på den drillede releasen,
        rullbakk- og kandidatleddet — holdes likt, så det eneste som
        skiller kjøringene er signaturen.
        """
        migrator.execute("RESET ROLE")
        oid = k["oppdrag"](signatur=signatur)
        k["artefakt"]("r-drillet", "promotert", oid)
        migrator.commit()
        did = _drill(migrator, k, nokkel="n-" + secrets.token_hex(6),
                     opp={**k["opp"], "inflight": oid})
        migrator.execute("RESET ROLE")
        rad = migrator.execute(
            "SELECT rene_utfall_ok FROM moduldrill WHERE drill_id=%s",
            (did,)).fetchone()[0]
        migrator.rollback()
        return rad

    for navn, signatur in (
        ("signaturen mangler", False),
        ("signaturen er tom", "   "),
        ("signaturen er en annen enn konvoluttens", "ff" * 16),
    ):
        assert maal(signatur) is False, \
            f"porten kaller utfallet rent når {navn}"

    # Motprøven, gjennom nøyaktig samme vei: det ENESTE som endres er at
    # kolonnen bærer konvoluttens egen signaturverdi. Uten den ville
    # testen over også bestått av en port som avviste alt.
    assert maal(True) is True, \
        "porten avviser den ekte, signerte formen"


@pg
def test_rent_utfall_krever_avtrykket_verifiseringsveien_setter_igjen(
        migrator):
    """Codex' P1 (runde 15): de tre feltene eies av SAMME skriver.

    Runde 8 gjorde «signert» til en likhet mellom `kvittering_signatur`,
    konvoluttens `signatur.verdi` og en satt `resultathash`. Men
    kjøretidsrollen har direkte `UPDATE` på oppdragsraden — det er den
    API-et selv bruker — så én `UPDATE oppdrag SET kvittering=
    '{"signatur":{"verdi":"x"}}', kvittering_signatur='x',
    resultathash='x'` oppfyller hele likheten uten at noen konvolutt er
    verifisert. Feltenighet mellom kolonner én rolle skriver fritt, er
    ikke et bevis; det er en form som er litt mer arbeid å fylle ut.

    Porten krever nå avtrykket UTENFOR raden: kvitteringskapabiliteten
    for oppdraget må være BRENT, med nøyaktig radens `resultathash`.
    `kvitteringskapabiliteter` står `REVOKE ALL ... FROM PUBLIC` (005)
    uten et eneste tabellgrant — den fylles bare av
    `utsted_kvitteringskapabilitet` (krever en claim kalleren holder) og
    brennes bare av `bruk_kvitteringskapabilitet`, som API-et kaller
    FØRST etter at `attestering.verifiser` har godtatt signaturen.

    Tre former må falle, og de skiller seg fra hverandre: ingen
    kapabilitet i det hele tatt, en utstedt som aldri ble brent, og en
    brent på et ANNET resultat enn det raden bærer.
    """
    k = _kjede(migrator)

    def maal(kapabilitet):
        migrator.execute("RESET ROLE")
        oid = k["oppdrag"](kapabilitet=kapabilitet)
        k["artefakt"]("r-drillet", "promotert", oid)
        migrator.commit()
        did = _drill(migrator, k, nokkel="n-" + secrets.token_hex(6),
                     opp={**k["opp"], "inflight": oid})
        migrator.execute("RESET ROLE")
        rad = migrator.execute(
            "SELECT rene_utfall_ok FROM moduldrill WHERE drill_id=%s",
            (did,)).fetchone()[0]
        migrator.rollback()
        return rad

    for navn, kapabilitet in (
        ("kvitteringen er skrevet rett på raden, uten kapabilitet", False),
        ("kapabiliteten er utstedt, men aldri brent", "utstedt"),
        ("kapabiliteten er brent på et annet resultat", "annen_hash"),
    ):
        assert maal(kapabilitet) is False, \
            f"porten kaller utfallet rent når {navn}"

    # Motprøven: brent med radens egen resultathash — nøyaktig det
    # `_forbruk_kapabilitet` gjør etter at signaturen er verifisert.
    assert maal(True) is True, \
        "porten avviser den ekte formen verifiseringsveien etterlater"

    # …og fullmakten er MINST MULIG: målingen leser fire kolonner, ikke
    # jti-en eller claim-identiteten.
    migrator.execute("RESET ROLE")
    kolonner = {r[0] for r in migrator.execute(
        "SELECT a.attname FROM pg_attribute a"
        " WHERE a.attrelid = 'kvitteringskapabiliteter'::regclass"
        "   AND a.attnum > 0 AND NOT a.attisdropped"
        "   AND has_column_privilege('disponit_modul_eier', a.attrelid,"
        "                            a.attnum, 'SELECT')").fetchall()}
    assert kolonner == {"tenant", "oppdrag_id", "status", "resultathash"}, \
        kolonner
    assert not migrator.execute(
        "SELECT has_table_privilege('disponit_modul_eier',"
        " 'kvitteringskapabiliteter', 'INSERT')").fetchone()[0], \
        "definern skal MÅLE kapabiliteten, aldri kunne skrive den"
    migrator.rollback()

    # …og DRILLSONDEN må stille samme krav (Codex P2, runde 15). Gjør den
    # ikke det, gir en forfalsket kvittering et grønt rullbakk-artefakt
    # av en drill som alt har konsumert release-IDene — og aksepten
    # avviser samme utfall etterpå. En drill som rapporterer bestått for
    # evidens som ikke kan aksepteres, måler noe annet enn aksepten.
    #
    # …og den stiller det gjennom SAMME FUNKSJON (Codex P1, runde 17).
    # Sonden spurte basen direkte som `disponit_migrator`, og tabellen er
    # `REVOKE ALL … FROM PUBLIC` med kolonnegrant bare til modul_eier —
    # spørringen dør på «permission denied», ETTER at rullingen har
    # drenert den levende deploymenten. Predikatet bor i basen nå.
    drill = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    assert "maal_rent_utfall" in drill, \
        "drillsonden måler ikke gjennom den delte porten"
    assert "kvitteringskapabiliteter k" not in drill, \
        "sonden har fortsatt sin egen kopi av predikatet"
    sjekkliste = (ROT / "deploy/staging/wcag-staging-sjekkliste.py").read_text(
        encoding="utf-8")
    assert "maal_rent_utfall" in sjekkliste, \
        "sjekklistens signaturmåling går utenom den delte porten"
    m049 = M049.read_text(encoding="utf-8")
    kropp = m049[m049.index("FUNCTION maal_rent_utfall"):]
    kropp = kropp[:kropp.index("END $$;")]
    assert "kvitteringskapabiliteter k" in kropp and \
        "k.status = 'brukt'" in kropp, \
        "den delte målingen krever ikke avtrykket lenger"


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


class _Manifesthash:
    """Bare det `verifiser_registrert_manifest` spør om: hash per release."""

    def __init__(self, hash_per_release):
        self.hasher, self._svar = hash_per_release, None

    def execute(self, _sql, params=None):
        self._svar = self.hasher.get(params[1]) if params else None
        return self

    def fetchone(self):
        return (self._svar,) if self._svar is not None else None


def _manifesthistorikk(m, tmp_path, generasjoner):
    """Et lite repo med én commit per manifestgenerasjon. -> commit-shaer.

    Historikken bygges, den lånes ikke: CI sjekker ut grunt (én commit),
    og en test som leter etter en ELDRE generasjon i utsjekkingens egen
    historikk ville da måttet hoppe over seg selv — og et hopp er ikke en
    port. `_git` kjører mot modulens `REPO`, så den peker hit.
    """
    (tmp_path / m.MANIFEST_REL).parent.mkdir(parents=True, exist_ok=True)

    def kjor(*argv):
        r = subprocess.run(["git", "-C", str(tmp_path), *argv],
                           capture_output=True)
        assert r.returncode == 0, r.stderr.decode()
        return r.stdout.decode().strip()

    kjor("init", "-q", "-b", "hoved")
    kjor("config", "user.email", "drill@disponit.test")
    kjor("config", "user.name", "drill")
    shaer = []
    for i, raa in enumerate(generasjoner):
        (tmp_path / m.MANIFEST_REL).write_bytes(raa)
        kjor("add", m.MANIFEST_REL)
        kjor("commit", "-q", "-m", f"generasjon {i}")
        shaer.append(kjor("rev-parse", "HEAD"))
    return shaer


def test_akseptens_manifest_maa_vaere_det_releasene_ble_registrert_fra():
    """Codex' P1 (runde 7): digestkjeden ble bundet, manifestet gikk fri.

    `manifest_hash` er sha256 av manifestet slik det så ut da releasen
    ble REGISTRERT i drillens fase 2, og akseptraden peker på
    `manifest_commit`. De to divergerer med nødvendighet — drillartefaktet
    bindes inn i manifestet ETTER drillen — så ingenting hindret en
    aksept i å peke på en pen commit mens den aksepterte deploymenten
    kjørte en helt annen modulkonfigurasjon. Like image-bytes reparerer
    ikke det: manifestet er identiteten, imaget er bytene.
    """
    m = _aksept_skript()
    manifest, sha = m.les_manifest()
    hode = subprocess.run(["git", "-C", str(ROT), "rev-parse", "HEAD"],
                          capture_output=True).stdout.decode().strip()
    releaser = ("r-drillet", "r-kandidat")
    # Samme generasjon som akseptcommiten: proveniensen er commiten selv.
    assert m.verifiser_registrert_manifest(
        _Manifesthash({r: sha for r in releaser}), releaser,
        manifest, sha, hode) == hode
    # Halve drillen i en annen generasjon er ingen drill.
    with pytest.raises(SystemExit) as ei:
        m.verifiser_registrert_manifest(
            _Manifesthash({"r-drillet": sha, "r-kandidat": "b" * 64}),
            releaser, manifest, sha, hode)
    assert "ULIKE manifestgenerasjoner" in str(ei.value)
    # En generasjon som ikke er sjekket inn, kan ingen lese.
    with pytest.raises(SystemExit) as ei:
        m.verifiser_registrert_manifest(
            _Manifesthash({r: "d" * 64 for r in releaser}), releaser,
            manifest, sha, hode)
    assert "innsjekket generasjon" in str(ei.value)
    # Raden må finnes i det hele tatt.
    with pytest.raises(SystemExit) as ei:
        m.verifiser_registrert_manifest(
            _Manifesthash({"r-drillet": sha}), releaser, manifest, sha, hode)
    assert "finnes ikke" in str(ei.value)


def test_evidensbindingen_er_den_ene_tillatte_manifestendringen(
        monkeypatch, tmp_path):
    """…og drillen må ellers ha kjørt NØYAKTIG den aksepterte modulen.

    Mellom drill og aksept endres manifestet én gang, av flyten selv:
    drillartefaktet bindes inn i `staging_sjekkliste`, og akseptcommitens
    manifest er derfor en annen generasjon enn kandidatreleasens. Den ene
    endringen skal slippe gjennom. Alt annet — `status`,
    `driftstilstand`, `kjerne`, avhengigheter — er modulens kjørende
    identitet, og en endring der er en NY release som må registreres og
    drilles for seg.
    """
    import yaml
    m = _aksept_skript()
    drillet = {"id": "wcag_audit", "navn": "WCAG", "versjon": "0.1.0",
               "status": "under_utvikling", "driftstilstand": "ikke_i_drift",
               "avhengigheter": ["m01_policy"], "kjerne": "platform/modules/m",
               "i18n_prefiks": "wcagaudit",
               "staging_sjekkliste": {"rollback_testet": {"status": "nei"}}}
    # Akseptcommitens manifest: samme modul, med drillartefaktet bundet inn.
    akseptert = dict(drillet, staging_sjekkliste={
        "rollback_testet": {"status": "ja", "artefakt_sha256": "ab" * 32}})
    raa = [yaml.safe_dump(g, sort_keys=True).encode("utf-8")
           for g in (drillet, akseptert)]
    gammel_commit, hode = _manifesthistorikk(m, tmp_path, raa)
    monkeypatch.setattr(m, "REPO", tmp_path)
    gammel_sha = hashlib.sha256(raa[0]).hexdigest()
    ny_sha = hashlib.sha256(raa[1]).hexdigest()
    releaser = ("r-drillet", "r-kandidat")
    conn = _Manifesthash({r: gammel_sha for r in releaser})
    # Bare evidensbindingen skiller: aksepten bærer den generasjonen, og
    # sier hvilken commit den ble sjekket inn i.
    assert m.verifiser_registrert_manifest(
        conn, releaser, akseptert, ny_sha, hode) == gammel_commit
    # Identiteten er flyttet: da er dette en annen modul enn den drillede.
    for felt, verdi in (("status", "aktiv"),
                        ("driftstilstand", "produksjon"),
                        ("kjerne", "platform/modules/en_annen"),
                        ("avhengigheter", [])):
        with pytest.raises(SystemExit) as ei:
            m.verifiser_registrert_manifest(
                conn, releaser, dict(akseptert, **{felt: verdi}), ny_sha,
                hode)
        assert felt in str(ei.value) and "NY release" in str(ei.value)
    # …og en generasjon som ikke er sjekket inn, finnes ikke å lese.
    with pytest.raises(SystemExit) as ei:
        m.verifiser_registrert_manifest(
            _Manifesthash({r: "e" * 64 for r in releaser}), releaser,
            akseptert, ny_sha, hode)
    assert "innsjekket generasjon" in str(ei.value)


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


class _Barn:
    """Så mye av en `Popen` som `_kjor_faser` rører: den svarer med én
    gang, så pollesløyfa går én runde."""

    def __init__(self, returncode=0, ut="", feil=""):
        self.returncode, self._ut, self._feil = returncode, ut, feil

    def communicate(self, timeout=None):
        return self._ut, self._feil

    def kill(self):
        self.returncode = -9


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


def test_drillen_nekter_samme_id_for_rullback_og_kandidat():
    """Codex' P2 (runde 7): to UBRUKTE, men LIKE id-er passerte porten.

    Drillen måler at rullbakken drenerES og at kandidaten OVERTAR, og én
    deployment kan ikke være begge. Med samme id drenerer første
    `bytt_release` den levende releasen og gjør den delte id-en claiming;
    kandidatbyttet blir en no-op, og etterkontrollen leser samme rad som
    både `draining` og `claiming`. Artefaktet er da garantert rødt — men
    først etter at originaldeploymenten er brukt opp og staging kjører
    rullbakk-bytene. Porten står før rullingen, ikke etter."""
    d = _drillskript()
    tom = _Livslop()
    with pytest.raises(SystemExit) as ei:
        d.krev_ubrukte_drillreleaser(tom, "r-drillet", "r-samme", "r-samme")
    assert "samme release" in str(ei.value)
    # …og den må stoppe FØR deploymentoppslaget, som ikke ser noe galt.
    with pytest.raises(SystemExit) as ei:
        d.krev_ubrukte_drillreleaser(_Livslop(), "r-drillet", "r-x", "r-x")
    assert "ULIKE id-er" in str(ei.value)


def test_drillen_apner_artefaktmaalet_for_den_odelegger_noe(tmp_path):
    """Codex' P2 (runde 8): `--ut` ble først rørt på siste linje.

    Da hadde begge `bytt_release`-ene gått og alle drilljobbene brukt opp
    sine engangs-deploymentIDer. Livsløpet er enveis, så en manglende
    eller uskrivbar foreldermappe ga en FULLFØRT destruktiv drill og
    ingen måling — og pekte stien på en eksisterende evidensfil, ble en
    tidligere drills artefakt stille overskrevet. Begge avgjøres nå før
    noe er registrert eller rullet."""
    d = _drillskript()

    # Manglende foreldermappe: stoppes, og mappen lages IKKE — en drill
    # som ikke kan gjentas skal stoppe på en feilskrevet sti.
    borte = tmp_path / "finnes-ikke" / "art.json"
    with pytest.raises(SystemExit) as ei:
        d.reserver_artefaktmaal(borte)
    assert "finnes ikke" in str(ei.value)
    assert not borte.parent.exists()

    # Eksisterende evidens: stoppes, og filen er urørt etterpå.
    fantes = tmp_path / "tidligere.json"
    fantes.write_text('{"bestatt": true}\n', encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        d.reserver_artefaktmaal(fantes)
    assert "finnes alt" in str(ei.value)
    assert fantes.read_text(encoding="utf-8") == '{"bestatt": true}\n'

    # Den gyldige veien: reservasjonen er en EGEN, tom fil — målet er
    # fortsatt ledig — og artefaktet havner der til slutt.
    ut = tmp_path / "art.json"
    delvis = d.reserver_artefaktmaal(ut)
    assert delvis.exists() and delvis.read_bytes() == b"" and delvis != ut
    assert not ut.exists()
    d.skriv_artefakt(delvis, ut, '{"bestatt": false}\n')
    assert ut.read_text(encoding="utf-8") == '{"bestatt": false}\n'
    assert not delvis.exists()

    # Og dukker det opp evidens på målet MENS drillen kjører, kastes ikke
    # målingen: flyttingen nekter, og delvisfilen blir liggende med den.
    delvis2 = d.reserver_artefaktmaal(tmp_path / "art2.json")
    (tmp_path / "art2.json").write_text("annen evidens\n", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        d.skriv_artefakt(delvis2, tmp_path / "art2.json", '{"m": 1}\n')
    assert str(delvis2) in str(ei.value)
    assert delvis2.read_text(encoding="utf-8") == '{"m": 1}\n'
    assert (tmp_path / "art2.json").read_text(
        encoding="utf-8") == "annen evidens\n"


def test_bare_en_flippedrill_av_gangen(tmp_path):
    """Codex' P1 (runde 9): reservasjonen gjerdet ikke parallelle driller.

    Runde 8 la inn `O_EXCL` på en delvisfil og kalte den et vern mot to
    samtidige kjøringer. Navnet bar PID-en, så hver prosess fikk sin egen
    sti og kollisjonen kunne per konstruksjon aldri skje — og selv uten
    PID-en dekker en fil bare ett `--ut`. Ingen modulbred lås fantes:
    `bytt_release` låser hver overgang og slipper, og drillen bor mellom
    overgangene. To kjøringer leser da samme claimende release, og den
    andres rulling drenerer den førstes rullbakk-deployment midt i (b2).
    Livsløpet er enveis, så begge id-parene er brukt opp etterpå."""
    d = _drillskript()

    # 1) Filreservasjonen betyr nå det den sier: stien er en funksjon av
    #    `--ut` alene, og andre forsøk på samme mål kolliderer.
    ut = tmp_path / "art.json"
    delvis = d.reserver_artefaktmaal(ut)
    assert str(os.getpid()) not in delvis.name, \
        "PID-en i navnet gjør O_EXCL til en reservasjon uten motpart"
    with pytest.raises(SystemExit) as ei:
        d.reserver_artefaktmaal(ut)
    assert "alt reservert" in str(ei.value)
    assert delvis.exists(), "den førstes reservasjon skal stå urørt"

    class _Laas:
        """Så mye av forbindelsen som reservasjonsporten rører.

        `fikk` er svaret på drillåsen; `sprekk` er unntaket
        `ta_deployreservasjon` eventuelt kaster.
        """

        def __init__(self, fikk, sprekk=None):
            self.fikk, self.sprekk, self.kall = fikk, sprekk, []

        def execute(self, sql, params=None):
            self.kall.append((sql, params))
            if self.sprekk and "ta_deployreservasjon" in sql:
                raise self.sprekk
            return self

        def fetchone(self):
            return (self.fikk,)

        def commit(self):
            self.kall.append(("COMMIT", None))

        def rollback(self):
            self.kall.append(("ROLLBACK", None))

    def _sporringer(laas):
        return [(s, p) for s, p in laas.kall
                if s not in ("COMMIT", "ROLLBACK")]

    # 2) Låsen er den egentlige porten — og den avviser, den venter ikke:
    #    en drill som står i kø starter mot en helt annen tilstand enn
    #    den leste argumentene sine for.
    holdt = _Laas(False)
    with pytest.raises(SystemExit) as ei:
        d.ta_drillereservasjonen(holdt)
    assert "drillåsen" in str(ei.value)
    (sql, params), = _sporringer(holdt)
    assert "pg_try_advisory_lock" in sql, \
        "blokkerende lås: drillen ville ventet og målt en annen tilstand"
    assert "_xact_" not in sql, \
        "transaksjonslåsen slippes ved første commit — drillen har mange"
    assert params == (d.DRILLNOKKEL, f"{d.MODUL}:{d.MILJO}")

    # 2b) DRILLÅSEN GJERDER BARE EN ANNEN DRILL (Codex P1, runde 14).
    #     Nøkkelen over står i advisory-låsens to-heltallsrom; registerets
    #     overganger — `bytt_release`, `noddeaktiver_modul`,
    #     `reaktiver_modul`, `sett_modulstatus` — tar
    #     `hashtextextended('modul:' || modul_id, 0)`, et annet rom. En
    #     vanlig deployment så derfor aldri drillens reservasjon og kunne
    #     drenere rullbakk- eller kandidatdeploymenten mellom to faser.
    #
    #     Reservasjonen kan ikke være en advisory-lås i DET rommet heller:
    #     eksklusivt stenger den claim-porten i 015 (som tar den samme
    #     nøkkelen delt for hvert claim, og drillen måler nettopp
    #     claiming), delt stenger den drillens EGNE overganger, som går i
    #     egne prosesser med egne sesjoner. Derfor: en rad med et token.
    fjortende = (ROT / "platform/core/db/migrations/014_modulregister.sql"
                 ).read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id," \
        " 0))" in fjortende, \
        "014 har byttet modul-låsenøkkel — reservasjonen må vurderes på nytt"
    femtende = (ROT / "platform/core/db/migrations/"
                "015_modulregister_claim.sql").read_text(encoding="utf-8")
    assert "hashtextextended('modul:' || r.eiermodul, 0)" in femtende, \
        "claim-porten tar ikke lenger modulnøkkelen — reservasjonsformen" \
        " må vurderes på nytt"

    # Porten står på TABELLEN, ikke i de funksjonene noen husket å endre:
    # en trigger på `moduldeployment` gjelder enhver skrivevei inn.
    nittiende = (ROT / "platform/core/db/migrations/049_modulaksept.sql"
                 ).read_text(encoding="utf-8")
    assert "CREATE TABLE moduldeployment_reservasjon" in nittiende
    assert "BEFORE INSERT OR UPDATE ON moduldeployment" in nittiende, \
        "reservasjonsvakten dekker ikke hele skriveflaten"
    assert "current_setting('disponit.deployreservasjon', true)" in nittiende
    assert "utloper_ts" in nittiende, \
        "uten utløp stenger en død drill modulen for alltid"

    # …og drillen tar den, med et token den også legger i MILJØET, så
    # sjekklistefasenes egne prosesser slipper gjennom sitt eget gjerde.
    ledig = _Laas(True)
    try:
        d.ta_drillereservasjonen(ledig)
        token = os.environ.get("DISPONIT_DEPLOYRESERVASJON", "")
        assert token and token == d.RESERVASJONSTOKEN, \
            "fasene arver ikke tokenet — drillen stoppes av sitt eget gjerde"
        tatt = [(s, p) for s, p in _sporringer(ledig)
                if "ta_deployreservasjon" in s]
        assert len(tatt) == 1 and tatt[0][1] == (
            d.MODUL, d.MILJO, token, d.RESERVASJONSVARIGHET)
        assert any("set_config('disponit.deployreservasjon'" in s
                   and p == (token,) for s, p in _sporringer(ledig)), \
            "drillens egen sesjon presenterer ikke reservasjonen"
        assert any(",false)" in s for s, p in _sporringer(ledig)
                   if "disponit.deployreservasjon" in s), \
            "transaksjonsskopet GUC-et faller ved første commit"
    finally:
        atexit.unregister(d.frigi_drillereservasjonen)
        os.environ.pop("DISPONIT_DEPLOYRESERVASJON", None)
        d.RESERVASJONSTOKEN = ""

    # Er flaten alt reservert, avvises drillen — den skal ikke måle et
    # register som flytter seg under den.
    opptatt = _Laas(True, psycopg.errors.LockNotAvailable("holdt av en annen"))
    try:
        with pytest.raises(SystemExit) as ei:
            d.ta_drillereservasjonen(opptatt)
        assert "reservert" in str(ei.value)
        assert "DISPONIT_DEPLOYRESERVASJON" not in os.environ, \
            "tokenet ble eksportert selv om reservasjonen ikke ble tatt"
    finally:
        atexit.unregister(d.frigi_drillereservasjonen)
        d.RESERVASJONSTOKEN = ""

    # Sjekklisten presenterer tokenet på HVER forbindelse den åpner —
    # ellers står drillens egne faser 2/4/9 utenfor sin egen reservasjon.
    sjekk_kode = (ROT / "deploy/staging/wcag-staging-sjekkliste.py").read_text(
        encoding="utf-8")
    pg_kropp = sjekk_kode.split("def _pg(", 1)[1].split("\ndef ", 1)[0]
    assert "DISPONIT_DEPLOYRESERVASJON" in pg_kropp \
        and "set_config('disponit.deployreservasjon'" in pg_kropp, \
        "fasene åpner forbindelser uten å presentere reservasjonen"

    # Drillåsen slippes ALDRI underveis — den skal holdes av sesjonen til
    # prosessen dør.
    assert not any("advisory_unlock" in s for s, _ in ledig.kall)
    tekst = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    assert "advisory_unlock" not in tekst, \
        "slippes låsen underveis, er resten av drillen ugjerdet"

    # 3) …og den tas FØR tilstanden leses. Tas den etterpå, har begge
    #    kjøringene alt lest samme claimende release, og taperen avbryter
    #    først når den har bestemt seg for hva den skal drille.
    kropp = tekst[tekst.index("\ndef main()"):]
    assert (kropp.index("ta_drillereservasjonen(m")
            < kropp.index("den_ene_claimende(m)")), \
        "låsen må stå foran oppslaget den beskytter"


def test_reservasjonen_fornyes_og_maales_gjennom_hele_drillen(tmp_path,
                                                              monkeypatch):
    """Codex' P2 (runde 16): gjerdet ble tatt med et fast utløp på to timer
    og aldri sett på igjen.

    `_kjor_faser` kalte i tillegg `subprocess.run` UTEN frist, så en fase
    som hang sto i det uendelige — og basens vakt ignorerer med vilje en
    utløpt reservasjonsrad. En vanlig `bytt_release` kunne da drenere
    drillens deployment, hvorpå den hengende fasen fortsatte i det enveis
    løpet mot en tilstand ingen måling beskriver.

    Tre ting må derfor stemme: hjerteslaget fornyer, porten MÅLER at
    gjerdet står foran hvert enveis steg, og en fase som henger avbrytes
    framfor å stå."""
    d = _drillskript()

    # 1) Tallene må henge sammen: flere slag skal rekke å feile før
    #    marginen er brukt opp, og marginen skal være ekte tid igjen.
    assert 0 < d.RESERVASJONSMARGIN_S < d.RESERVASJONSVARIGHET_S
    assert d.RESERVASJONSHJERTESLAG_S * 3 < (d.RESERVASJONSVARIGHET_S
                                             - d.RESERVASJONSMARGIN_S)
    assert d.RESERVASJONSVARIGHET == f"{d.RESERVASJONSVARIGHET_S} seconds"

    class _Hjerte:
        """Så mye av en forbindelse som hjerteslaget rører."""

        def __init__(self, *sprekker, stopp_etter=3):
            self.sprekker = list(sprekker)
            self.stopp_etter, self.forlengelser = stopp_etter, 0
            self.aapnet = self.lukket = 0

        def __call__(self, _dsn):
            self.aapnet += 1
            return self

        def execute(self, sql, params=None):
            if "forleng_deployreservasjon" in sql:
                self.forlengelser += 1
                if self.forlengelser >= self.stopp_etter:
                    d.HJERTESTOPP.set()
                if self.sprekker:
                    sprekk = self.sprekker.pop(0)
                    if sprekk is not None:
                        raise sprekk
            return self

        def commit(self):
            pass

        def close(self):
            self.lukket += 1

    monkeypatch.setattr(d, "RESERVASJONSHJERTESLAG_S", 0.001)
    monkeypatch.setattr(d, "RESERVASJONSTOKEN", "drill-token")

    # 2) Den normale gangen: slagene fornyer, og gjerdet står.
    d.SISTE_FORNYELSE = time.monotonic() - 10_000
    hjerte = _Hjerte()
    d._hjerteslag("dsn", kobler=hjerte)
    assert hjerte.forlengelser == 3 and hjerte.lukket == 1
    assert d.RESERVASJONEN_TAPT == ""
    d.krev_reservasjonen("rullingen")        # fornyet nettopp → porten er åpen

    # 3) Et forbigående blaff dreper ikke en gyldig drill: slaget prøver
    #    igjen på en frisk forbindelse, og MARGINEN avgjør — ikke slaget.
    d.HJERTESTOPP.clear()
    d.RESERVASJONEN_TAPT = ""
    blaff = _Hjerte(OSError("basen startet på nytt"), stopp_etter=3)
    d._hjerteslag("dsn", kobler=blaff)
    assert blaff.forlengelser == 3, "ett mislykket slag stoppet hjertet"
    assert blaff.aapnet == 2, "forbindelsen ble ikke åpnet på nytt"
    assert d.RESERVASJONEN_TAPT == ""
    d.krev_reservasjonen("rullingen")

    # 4) …men er reservasjonen UTLØPT eller OVERTATT, hjelper ingen
    #    margin: en deployment kan alt ha gått i vinduet.
    d.HJERTESTOPP.clear()
    d.RESERVASJONEN_TAPT = ""
    tapt = _Hjerte(psycopg.errors.LockNotAvailable("holdes ikke av deg"),
                   stopp_etter=99)
    d._hjerteslag("dsn", kobler=tapt)
    assert tapt.forlengelser == 1, "hjertet slo videre på et tapt gjerde"
    assert "holdes ikke av deg" in d.RESERVASJONEN_TAPT
    with pytest.raises(SystemExit) as ei:
        d.krev_reservasjonen("rullingen")
    assert "rullingen" in str(ei.value) and "reservasjon" in str(ei.value)

    # 5) Stillhet alene stopper også — mens gjerdet ENNÅ står, med
    #    marginen igjen.
    d.RESERVASJONEN_TAPT = ""
    d.SISTE_FORNYELSE = time.monotonic() - (d.RESERVASJONSVARIGHET_S
                                            - d.RESERVASJONSMARGIN_S + 1)
    with pytest.raises(SystemExit) as ei:
        d.krev_reservasjonen("rullingen")
    assert "fornyelse" in str(ei.value)
    # Ingen reservasjon tatt (tørrkjøring): porten er ikke der.
    monkeypatch.setattr(d, "RESERVASJONSTOKEN", "")
    d.krev_reservasjonen("rullingen")

    # 6) En fase som HENGER avbrytes — den skal ikke stå og la
    #    reservasjonen tikke mot utløpet. Målt på en ekte prosess.
    (tmp_path / "wcag-staging-sjekkliste.py").write_text(
        "import time\ntime.sleep(120)\n", encoding="utf-8")
    monkeypatch.setattr(d, "HER", tmp_path)
    monkeypatch.setattr(d, "PINNET_MOTORIMAGE", "sha256:" + "aa" * 32)
    monkeypatch.setattr(d, "FASEFRIST_S", 1)
    monkeypatch.setattr(d, "FASEPOLL_S", 0.2)
    t0 = time.monotonic()
    with pytest.raises(SystemExit) as ei:
        d._kjor_faser("wcag-r99", tmp_path / "e.jsonl", hva="rullbakken")
    assert time.monotonic() - t0 < 30, "fristen stoppet ikke den hengende fasen"
    assert "uten å bli ferdig" in str(ei.value)

    # 6b) …og et GJERDE SOM FALLER MENS fasen kjører dreper den med én
    #     gang (Codex P2, runde 17). `FASEFRIST_S` er lengre enn
    #     reservasjonen, så en port bare før og etter fasen kommer for
    #     sent: hjerteslaget kan miste basen rett etter forrige port, og
    #     en vanlig `bytt_release` gå mens fasen fortsatt kjører.
    monkeypatch.setattr(d, "FASEFRIST_S", 600)
    monkeypatch.setattr(d, "RESERVASJONSTOKEN", "drill-token")
    d.RESERVASJONEN_TAPT = ""
    d.SISTE_FORNYELSE = time.monotonic()

    def _mist_gjerdet(_delay=0.6):
        time.sleep(_delay)
        d.RESERVASJONEN_TAPT = "reservasjonen ble overtatt"

    tapt = threading.Thread(target=_mist_gjerdet, daemon=True)
    t0 = time.monotonic()
    tapt.start()
    with pytest.raises(SystemExit) as ei:
        d._kjor_faser("wcag-r99", tmp_path / "e.jsonl", hva="rullbakken")
    assert time.monotonic() - t0 < 30, \
        "fasen fikk fortsette etter at gjerdet falt"
    assert "overtatt" in str(ei.value) and "uten gjerde" in str(ei.value)
    d.RESERVASJONEN_TAPT = ""

    # 7) …og porten står foran hvert enveis steg i `main()`.
    tekst = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    kropp = tekst[tekst.index("\ndef main()"):]
    assert (kropp.index("krev_reservasjonen(\"registreringen")
            < kropp.index("registrer_drillrelease(m")), \
        "registreringen av drill-id-ene står utenfor gjerdet"
    assert (kropp.index("krev_reservasjonen(\"rullingen\")")
            < kropp.index("SELECT bytt_release")), \
        "rullingen er destruktiv og må måle gjerdet rett før den fyres"
    fasekropp = tekst[tekst.index("\ndef _kjor_faser("):
                      tekst.index("\ndef ", tekst.index("\ndef _kjor_faser(")
                                  + 1)]
    assert "communicate(timeout=FASEPOLL_S)" in fasekropp \
        and "barn.kill()" in fasekropp, \
        "fasen kan ikke avbrytes mens den kjører — porten kommer for sent"
    assert fasekropp.count("krev_reservasjonen(") == 2, \
        "gjerdet må måles både før og etter hver fase"
    assert "_reservasjonssvikt()" in fasekropp, \
        "gjerdet måles ikke MENS fasen kjører"


@pg
def test_reservasjonen_gjerder_hele_deploymentflaten(migrator):
    """Codex' P1 (runde 14): drillåsen lå i advisory-låsens
    to-heltallsrom, mens registerets overganger tar
    `hashtextextended('modul:' || modul_id, 0)`. To ulike låserom — en
    vanlig `bytt_release` eller et `noddeaktiver_modul` så aldri
    drillens reservasjon, og kunne drenere rullbakk- eller
    kandidatdeploymenten MELLOM to drillfaser. Målingene blir da noe
    annet enn de sier, og de enveis drill-id-ene er brukt opp uansett.

    Porten står derfor på `moduldeployment` selv: den gjelder enhver
    skrivevei inn i flaten, ikke bare de funksjonene noen husket å endre,
    og innehaveren er et token drillens EGNE sesjoner kan presentere —
    fasene 2/4/9 kjører i egne prosesser, så en sesjonslås ville stengt
    drillen ute fra sitt eget gjerde."""
    k = _kjede(migrator)
    token = "drill-" + secrets.token_hex(6)

    def _drener():
        return migrator.execute(
            "UPDATE moduldeployment SET livslop='draining'"
            " WHERE modul_id=%s AND miljo='staging' AND livslop='claiming'",
            (k["mid"],))

    # Uten reservasjon merker ingen at triggeren finnes.
    _drener()
    migrator.rollback()

    migrator.execute("SET ROLE disponit_modules_admin")
    migrator.execute(
        "SELECT ta_deployreservasjon(%s,'staging',%s,'test','2 hours')",
        (k["mid"], token))
    migrator.execute("RESET ROLE")
    migrator.commit()
    try:
        # En overgang uten tokenet avvises — også for tabelleieren.
        with pytest.raises(psycopg.errors.LockNotAvailable):
            _drener()
        migrator.rollback()
        # …og en annen kan ikke ta reservasjonen fra innehaveren.
        migrator.execute("SET ROLE disponit_modules_admin")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            migrator.execute(
                "SELECT ta_deployreservasjon(%s,'staging','en-annen',"
                "'test','2 hours')", (k["mid"],))
        migrator.rollback()
        migrator.execute("RESET ROLE")
        # Innehaveren selv slipper gjennom sitt eget gjerde.
        migrator.execute(
            "SELECT set_config('disponit.deployreservasjon',%s,true)",
            (token,))
        _drener()
        migrator.rollback()
        # En UTLØPT reservasjon gjerder ingenting: en drill som dør uten å
        # frigi, skal ikke stenge modulen for alltid.
        migrator.execute("UPDATE moduldeployment_reservasjon"
                         " SET utloper_ts = now() - interval '1 minute',"
                         "     tatt_ts = now() - interval '3 hours'"
                         " WHERE modul_id=%s", (k["mid"],))
        _drener()
        migrator.rollback()
    finally:
        migrator.rollback()
        migrator.execute("SET ROLE disponit_modules_admin")
        migrator.execute("SELECT frigi_deployreservasjon(%s,'staging',%s)",
                         (k["mid"], token))
        migrator.execute("RESET ROLE")
        migrator.commit()
    # …og frigivelsen tok den faktisk bort.
    assert migrator.execute(
        "SELECT count(*) FROM moduldeployment_reservasjon WHERE modul_id=%s",
        (k["mid"],)).fetchone()[0] == 0
    _drener()
    migrator.rollback()


@pg
def test_reservasjonsfeilene_lekker_ikke_innehaver_tokenet(migrator):
    """Codex' P2 (runde 16): vakten navnga innehaver-tokenet i feilen.

    Å HOLDE tokenet er hele autorisasjonen: vakten slipper gjennom den som
    presenterer det i `disponit.deployreservasjon`, og en GUC kan enhver
    kaller sette selv. Sa avvisningen hvilket token som gjelder, kunne den
    avviste overgangen bare settes på nytt med verdien den nettopp fikk
    utlevert — og gjerdet var borte for nøyaktig den som prøvde å gå forbi
    det. Rollen som avvises har ingen lesetilgang til `innehaver`; da skal
    heller ikke feilen gi den bort.

    Det operatøren trenger — hvem som holder flaten og hvor lenge — står
    fortsatt i meldingen."""
    k = _kjede(migrator)
    token = "hemmelig-" + secrets.token_hex(12)
    migrator.execute("SET ROLE disponit_modules_admin")
    migrator.execute("SELECT ta_deployreservasjon(%s,'staging',%s,"
                     "'m56-drill','2 hours')", (k["mid"], token))
    migrator.execute("RESET ROLE")
    migrator.commit()
    try:
        # 1) Vakten på deploymentflaten.
        with pytest.raises(psycopg.errors.LockNotAvailable) as ei:
            migrator.execute(
                "UPDATE moduldeployment SET livslop='draining'"
                " WHERE modul_id=%s AND miljo='staging' AND livslop='claiming'",
                (k["mid"],))
        assert token not in str(ei.value), \
            "tokenet er adgangen — feilen deler den ut"
        assert "m56-drill" in str(ei.value), \
            "operatøren må fortsatt få vite hvem som holder flaten"
        migrator.rollback()

        # 2) …og porten mot å ta en annens reservasjon.
        migrator.execute("SET ROLE disponit_modules_admin")
        with pytest.raises(psycopg.errors.LockNotAvailable) as ei:
            migrator.execute("SELECT ta_deployreservasjon(%s,'staging',"
                             "'en-annen','ops','2 hours')", (k["mid"],))
        assert token not in str(ei.value)
        assert "m56-drill" in str(ei.value)
        migrator.rollback()
        migrator.execute("RESET ROLE")
    finally:
        migrator.rollback()
        migrator.execute("SET ROLE disponit_modules_admin")
        migrator.execute("SELECT frigi_deployreservasjon(%s,'staging',%s)",
                         (k["mid"], token))
        migrator.execute("RESET ROLE")
        migrator.commit()


@pg
def test_reservasjonen_er_ikke_runtimes_a_ta(migrator):
    """Port 9 for reservasjonen: kjøretidsrollen har verken EXECUTE på
    funksjonene eller DML på tabellen. Kunne den tatt en reservasjon,
    kunne den også stengt enhver deployment av enhver modul."""
    rt = _rt()
    try:
        for sql in ("SELECT ta_deployreservasjon('m','staging','t','a',"
                    "'1 hour')",
                    "SELECT forleng_deployreservasjon('m','staging','t',"
                    "'1 hour')",
                    "SELECT frigi_deployreservasjon('m','staging','t')",
                    "INSERT INTO moduldeployment_reservasjon (modul_id,"
                    " miljo, innehaver, aktor, utloper_ts) VALUES"
                    " ('m','staging','t','a', now()+interval '1 hour')",
                    "UPDATE moduldeployment_reservasjon SET utloper_ts ="
                    " now()+interval '9 hours'",
                    "DELETE FROM moduldeployment_reservasjon"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(sql)
            rt.rollback()
    finally:
        rt.close()


@pg
def test_reservasjonen_fornyes_og_kan_ikke_gjenopplives(migrator):
    """Codex' P2 (runde 16): reservasjonen hadde et FAST utløp og ingen
    fornyelse.

    En drill som tok lengre tid enn utløpet mistet gjerdet MENS den kjørte
    — og basens vakt ignorerer med vilje en utløpt rad, så en vanlig
    `bytt_release` kunne da drenere drillens deployment mellom to faser.
    Utløpet skal måle «innehaveren er borte», ikke «drillen er lang»:
    kort levetid, fornyet så lenge prosessen lever.

    Og fornyelsen er en UPDATE på en LEVENDE rad, aldri en ny INSERT: er
    reservasjonen først utløpt, KAN en deployment ha gått i vinduet, og å
    gjerde på nytt som om ingenting hadde skjedd ville skjult nettopp
    det."""
    k = _kjede(migrator)
    token = "drill-" + secrets.token_hex(6)

    def _utloper():
        return migrator.execute(
            "SELECT utloper_ts FROM moduldeployment_reservasjon"
            " WHERE modul_id=%s", (k["mid"],)).fetchone()

    migrator.execute("SET ROLE disponit_modules_admin")
    migrator.execute("SELECT ta_deployreservasjon(%s,'staging',%s,'test',"
                     "'60 seconds')", (k["mid"], token))
    migrator.execute("RESET ROLE")
    migrator.commit()
    try:
        kort = _utloper()[0]
        # Fornyelsen flytter utløpet fram — det er hele poenget.
        migrator.execute("SET ROLE disponit_modules_admin")
        migrator.execute("SELECT forleng_deployreservasjon(%s,'staging',%s,"
                         "'600 seconds')", (k["mid"], token))
        migrator.execute("RESET ROLE")
        migrator.commit()
        assert _utloper()[0] > kort

        # En annen innehaver forlenger ingenting.
        migrator.execute("SET ROLE disponit_modules_admin")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            migrator.execute("SELECT forleng_deployreservasjon(%s,'staging',"
                             "'en-annen','600 seconds')", (k["mid"],))
        migrator.rollback()
        migrator.execute("RESET ROLE")
        # …og en varighet som ikke er positiv, gjerder ingenting.
        migrator.execute("SET ROLE disponit_modules_admin")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            migrator.execute("SELECT forleng_deployreservasjon(%s,'staging',"
                             "%s,'0 seconds')", (k["mid"], token))
        migrator.rollback()
        migrator.execute("RESET ROLE")

        # UTLØPT: gjerdet er tapt, og fornyelsen skal si det — ikke ta det
        # opp igjen. I det vinduet kan en deployment ha gått.
        migrator.execute("UPDATE moduldeployment_reservasjon"
                         " SET utloper_ts = now() - interval '1 minute',"
                         "     tatt_ts = now() - interval '1 hour'"
                         " WHERE modul_id=%s", (k["mid"],))
        migrator.commit()
        migrator.execute("SET ROLE disponit_modules_admin")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            migrator.execute("SELECT forleng_deployreservasjon(%s,'staging',"
                             "%s,'600 seconds')", (k["mid"], token))
        migrator.rollback()
        migrator.execute("RESET ROLE")
        assert migrator.execute(
            "SELECT count(*) FROM moduldeployment_reservasjon"
            " WHERE modul_id=%s AND utloper_ts > now()",
            (k["mid"],)).fetchone()[0] == 0, \
            "en utløpt reservasjon ble gjenopplivet av fornyelsen"
    finally:
        migrator.rollback()
        migrator.execute("SET ROLE disponit_modules_admin")
        migrator.execute("SELECT frigi_deployreservasjon(%s,'staging',%s)",
                         (k["mid"], token))
        migrator.execute("RESET ROLE")
        migrator.commit()


def test_drillen_nekter_flere_claimende_kontraktlinjer():
    """Codex' P2 (runde 9): oppslaget så ut som «den claimende
    deploymenten», men var «en av dem».

    `en_claiming_per_kontrakt` er unik per (modul, miljø, kontraktversjon,
    kontrakt_hash) — flere kontraktlinjer kan altså stå claiming
    samtidig, helt lovlig. Uten kontraktvelger og uten `ORDER BY` kastet
    `fetchone()` stille resten, og drillen plukket en vilkårlig. Hele
    kjøringen henger på den raden: `bytt_release` drenerer bare
    deploymenten som matcher den VALGTE kontrakten, så den andre står
    levende og claimende gjennom drillen og kan plukke dens egne
    probeoppdrag. Målingene teller status, ikke hvem som tok oppdraget."""
    d = _drillskript()

    class _Claimende:
        def __init__(self, *rader):
            self.rader, self.sql = list(rader), None

        def execute(self, sql, params=None):
            self.sql = sql
            return self

        def fetchall(self):
            return self.rader

    en = ("wcag-r11", 1, "kh-a", "aa" * 32)
    base = _Claimende(en)
    assert d.den_ene_claimende(base) == en
    # Oppslaget må hente ALLE radene — `fetchone()` er nettopp feilen.
    assert "ORDER BY" in base.sql, "uten ordning er «den første» vilkårlig"
    assert "d.kontrakt_hash" in base.sql

    with pytest.raises(SystemExit) as ei:
        d.den_ene_claimende(_Claimende())
    assert "ingen claiming-deployment" in str(ei.value)

    # To lovlige kontraktlinjer: drillen velger ikke, den avbryter — og
    # sier hvilke den så, så operatøren kan rulle de øvrige ut.
    with pytest.raises(SystemExit) as ei:
        d.den_ene_claimende(_Claimende(en, ("wcag-r9", 2, "kh-b", "bb" * 32)))
    assert "2 claimende deployments" in str(ei.value)
    assert "wcag-r11" in str(ei.value) and "wcag-r9" in str(ei.value)

    # …og den står FØR alt som konsumerer noe: ingen rulling, ingen
    # registrering, ingen bestilling er gjort når den avbryter.
    tekst = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    # (`_Claimende` har ingen `fetchone` — kalles den, ryker testen på
    #  AttributeError, og det er nettopp den formen funnet gjaldt.)
    kropp = tekst[tekst.index("\ndef main()"):]
    assert (kropp.index("den_ene_claimende(m)")
            < kropp.index("registrer_drillrelease(m")
            < kropp.index("bytt_release")), \
        "porten må stå foran registreringene og rullingen"


def test_drillen_nekter_naar_forgjengerens_bytes_ikke_kan_bootes():
    """Codex' P1 (runde 6), skriptsiden.

    Sjekklistens fase 1 leser det LOKALT BYGDE `disponit-wcag-motor`, og
    fase 2 krever at den claimende deploymentens `artifact_digest` er
    nøyaktig det imaget. `_kjor_faser` kan derfor ikke boote noe annet
    image, og den drillede releasen kom fra samme bygg. Er forgjengerens
    bytes andre, kan drillen ikke prøve dem — og da skal den si det, ikke
    registrere rullbakken med de drillede bytene og kalle det en
    rullbakk. At m56-releasene i dag deler digest er A1s levende bevis,
    og nettopp derfor må likheten måles: den er en tilstand, ikke en
    garanti."""
    d = _drillskript()
    d.krev_bootbare_forgjengerbytes("wcag-r10", "aa" * 32, "aa" * 32,
                                    "aa" * 32)
    with pytest.raises(SystemExit) as ei:
        d.krev_bootbare_forgjengerbytes("wcag-r10", "bb" * 32, "aa" * 32,
                                        "aa" * 32)
    assert "wcag-r10" in str(ei.value)

    assert d.forgjengerens_bytes(_Historie(("wcag-r10", "aa" * 32)),
                                 "wcag-r11", 1, "kh") == ("wcag-r10",
                                                          "aa" * 32)
    # Ingen forgjenger: det finnes ingenting å rulle tilbake til.
    with pytest.raises(SystemExit) as ei:
        d.forgjengerens_bytes(_Historie(None), "wcag-r11", 1, "kh")
    assert "forgjenger" in str(ei.value)


class _Historie:
    """Bare det `forgjengerens_bytes` spør om: hendelses-IDen den drilledes
    egen `releasebytte` fikk, og forgjengerraden (eller ingen av dem) — men
    den HUSKER alle spørsmålene, så kontraktbindingen kan måles."""

    def __init__(self, rad, egen_id=42):
        self.rad, self.egen_id = rad, egen_id
        self.spor = []                       # [(sql, params), …]
        self._svar = None

    def execute(self, sql, params=None):
        self.spor.append((sql, params or ()))
        self._svar = ((self.egen_id,) if "max(id)" in sql else self.rad)
        return self

    def fetchone(self):
        return self._svar

    @property
    def sql(self):
        return self.spor[-1][0]

    @property
    def params(self):
        return self.spor[-1][1]


def test_forgjengeren_hentes_fra_den_drillede_kontraktlinjen():
    """Codex' P1 (runde 10): forgjengeroppslaget så hele modulens historie.

    `en_claiming_per_kontrakt` fører én linje per (modul, miljø,
    kontraktversjon, kontrakt_hash), og `den_ene_claimende` har alt valgt
    nøyaktig én av dem. Filtrerte oppslaget bare på modul, miljø,
    release-id og tid, kunne den seneste raden før den drillede tilhøre en
    annen kontraktslekt — og `bytt_release` opererer innen den valgte
    kontrakten. Da avbrøt drillen enten en gyldig kjøring, eller navnga
    (ved tilfeldig like digester) en forgjenger som ikke er linjens.
    """
    d = _drillskript()
    hist = _Historie(("wcag-r10", "aa" * 32))
    d.forgjengerens_bytes(hist, "wcag-r11", 3, "kh" * 32)
    # Kontrakten er med i BEGGE oppslagene: den drilledes egen overgang og
    # forgjengerens — ingen av dem får se en annen kontraktslekt.
    assert len(hist.spor) == 2
    for sql, params in hist.spor:
        assert "kontraktversjon=%s" in sql and "kontrakt_hash=%s" in sql
        assert 3 in params and "kh" * 32 in params
    # …og joinene mot deployment- og releaseraden bærer den videre.
    assert "d.kontraktversjon = h.kontraktversjon" in hist.sql
    assert "r.kontraktversjon = d.kontraktversjon" in hist.sql


@pg
def test_forgjengeren_leses_av_overgangsrekkefolgen(migrator, monkeypatch):
    """Codex' P2 (runde 16): innen kontraktlinjen sto forgjengeren igjen på
    `ORDER BY fra_ts DESC, release_id DESC`, og ingen av de to leddene er
    en rekkefølge.

    `moduldeployment.fra_ts` er `now()`, som er TRANSAKSJONSSTABIL: alle
    overganger i samme transaksjon får identisk tidsstempel. Da avgjør
    tie-brekket, og `release_id` er en TEKST — «wcag-r9» sorterer etter
    «wcag-r10». Forgjengeren ble dermed en vilkårlig eldre release i
    linjen, og rullbakken hadde båret HENNES bytes.

    `modulregister_hendelse.id` tikker per INSERT, ikke per transaksjon.
    Målingen her er derfor den ekte formen: tre `bytt_release` i ÉN
    transaksjon, med navn valgt slik at det gamle tie-brekket peker på feil
    release."""
    d = _drillskript()
    mid = "m_forgj_" + secrets.token_hex(3)
    kh = "kh-" + secrets.token_hex(6)
    migrator.execute("INSERT INTO modulhode (modul_id, status) VALUES"
                     " (%s,'aktiv')", (mid,))
    migrator.execute("INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
                     " kontrakt_hash, payload_schema_hash,"
                     " kvittering_schema_hash, sideeffektklasse,"
                     " reversibilitet) VALUES"
                     " (%s,1,%s,'ph','qh','ekstern_lesing','direkte')",
                     (mid, kh))
    digest = {"wcag-r9": "99" * 32, "wcag-r10": "10" * 32,
              "wcag-r11": "11" * 32, "wcag-r12": "12" * 32}
    for rel, dig in digest.items():
        migrator.execute("INSERT INTO modulrelease (modul_id, release_id,"
                         " kontraktversjon, kontrakt_hash, manifest_hash,"
                         " artifact_digest) VALUES (%s,%s,1,%s,'mh',%s)",
                         (mid, rel, kh, dig))
    # ALLE tre overgangene i SAMME transaksjon — det er nettopp der
    # tidsstemplene faller sammen.
    migrator.execute("SET ROLE disponit_modules_admin")
    for rel in ("wcag-r9", "wcag-r10", "wcag-r11"):
        migrator.execute("SELECT bytt_release(%s,'staging',%s,1,%s,'test')",
                         (mid, rel, kh))
    migrator.execute("RESET ROLE")
    # Forutsetningen MÅLES, ikke antas: ett eneste `fra_ts` på alle tre.
    assert migrator.execute(
        "SELECT count(DISTINCT fra_ts) FROM moduldeployment"
        " WHERE modul_id=%s AND miljo='staging'", (mid,)).fetchone()[0] == 1

    monkeypatch.setattr(d, "MODUL", mid)
    monkeypatch.setattr(d, "MILJO", "staging")
    # Overgangsrekkefølgen sier wcag-r10. Det gamle tie-brekket ville sagt
    # wcag-r9 — den sorterer sist på tekst, og fra_ts skiller ikke.
    assert d.forgjengerens_bytes(migrator, "wcag-r11", 1, kh) == (
        "wcag-r10", "10" * 32)
    assert migrator.execute(
        "SELECT release_id FROM moduldeployment WHERE modul_id=%s"
        "   AND miljo='staging' AND release_id <> 'wcag-r11'"
        " ORDER BY fra_ts DESC, release_id DESC LIMIT 1",
        (mid,)).fetchone()[0] == "wcag-r9", \
        "målingen mister sin kraft hvis det gamle tie-brekket traff riktig"

    # En deployment lagt inn UTENOM `bytt_release` har ingen overgang å
    # lese rekkefølgen av — da gjetter drillen ikke, den avbryter.
    migrator.execute("INSERT INTO moduldeployment (modul_id, release_id,"
                     " kontraktversjon, kontrakt_hash, miljo, livslop)"
                     " VALUES (%s,'wcag-r12',1,%s,'staging','draining')",
                     (mid, kh))
    with pytest.raises(SystemExit) as ei:
        d.forgjengerens_bytes(migrator, "wcag-r12", 1, kh)
    assert "releasebytte" in str(ei.value)

    # …og den FØRSTE releasen i linjen har ingen forgjenger.
    with pytest.raises(SystemExit) as ei:
        d.forgjengerens_bytes(migrator, "wcag-r9", 1, kh)
    assert "forgjenger" in str(ei.value)
    migrator.rollback()


def test_drillen_maaler_vertens_image_for_den_drenerer(monkeypatch):
    """Codex' P1 (runde 10): imaget bootveien faktisk bærer, ble aldri sett
    på før rullingen.

    `krev_bootbare_forgjengerbytes` sammenlignet to digester lest ut av
    REGISTERET, mens påstanden handlet om bytene på disken.
    `disponit-wcag-motor` er en flyttbar tag: et nytt `bygg.sh` mellom
    registreringen og drillen flytter den, og første inspeksjon skjedde da
    i sjekklistens fase 1/2 — etter at `bytt_release` hadde drenert den
    levende deploymenten. Fase 2 døde på immutabilitetskonflikten med den
    gamle arbeideren fenset og rullbakk-id-en brukt opp.
    """
    d = _drillskript()
    # Verten bærer forgjengerens bytes: drillen kan boote det den lover.
    d.krev_bootbare_forgjengerbytes("wcag-r10", "aa" * 32, "aa" * 32,
                                    "aa" * 32)
    # Taggen har flyttet seg — porten skal stoppe FØR rullingen.
    with pytest.raises(SystemExit) as ei:
        d.krev_bootbare_forgjengerbytes("wcag-r10", "aa" * 32, "aa" * 32,
                                        "cc" * 32)
    assert "disponit-wcag-motor" in str(ei.value)

    # …og porten kalles med vertens digest før noe registreres eller rulles.
    tekst = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    kropp = tekst[tekst.index("\ndef main()"):]
    assert (kropp.index("lokalt_motorimage()")
            < kropp.index("registrer_drillrelease(m")
            < kropp.index("bytt_release")), \
        "vertens image må måles foran registreringene og rullingen"

    # Digesten leses av samme kilde som fase 1: `docker image inspect`.
    class _Ut:
        returncode = 0
        stdout = "sha256:" + "ab" * 32

    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: _Ut())
    assert d.lokalt_motorimage() == "ab" * 32

    class _Mangler:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: _Mangler())
    with pytest.raises(SystemExit) as ei:
        d.lokalt_motorimage()
    assert "bygg.sh" in str(ei.value)


def test_drillen_pinner_motorimaget_for_hele_kjoringen(monkeypatch, tmp_path):
    """Codex' P1 (runde 11): porten leste taggen og slapp den igjen.

    `disponit-wcag-motor` er mutabel — `bygg.sh` retagger det samme navnet
    — og sjekklistens fase 1 kjøres på nytt i HVER invokasjon av skriptet
    (`main` kaller `fase1` uansett `--fase`). `_kjor_faser`-invokasjonene
    ligger etter at drillen har drenert den levende deploymenten og brukt
    opp rullbakk-ID-en, så en retagging i det vinduet ga fasene andre
    bytes enn porten godkjente — oppdaget først som en
    immutabilitetskonflikt i fase 2, med staging uten claimende arbeider.
    Preflightens image-ID er immutabel, og den skal bæres inn i hver fase.
    """
    d = _drillskript()

    class _Ut:
        returncode = 0
        stdout = "sha256:" + "ab" * 32

    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: _Ut())
    assert d.PINNET_MOTORIMAGE == "", "pinnen finnes ikke før den er målt"
    d.lokalt_motorimage()
    assert d.PINNET_MOTORIMAGE == "sha256:" + "ab" * 32

    # …og pinnen følger med inn i hver fase, som en referanse fase 1 selv
    # slår opp i stedet for taggen.
    miljoer = []

    def _fanget(_cmd, **kw):
        miljoer.append(kw["env"]["WCAG_MOTORIMAGE"])
        return _Barn()

    # Fasene kjøres nå som `Popen` og pollet, så gjerdet kan måles MENS de
    # kjører (Codex P2, runde 17).
    monkeypatch.setattr(d.subprocess, "Popen", _fanget)
    d._kjor_faser("r-kand", tmp_path / "e.jsonl", hva="kandidaten")
    assert miljoer == ["sha256:" + "ab" * 32] * 3

    # Uten en målt pinne kjøres ingen fase: da ville de lest taggen.
    monkeypatch.setattr(d, "PINNET_MOTORIMAGE", "")
    with pytest.raises(SystemExit) as ei:
        d._kjor_faser("r-kand", tmp_path / "e.jsonl", hva="kandidaten")
    assert "ikke pinnet" in str(ei.value)

    # Sjekklisten må FAKTISK bruke referansen — både i fase 1s oppslag og
    # i motorkommandoen fasene 5–7 måler med. Leste én av dem taggen
    # direkte, var pinnen bare pynt.
    sj = (ROT / "deploy/staging/wcag-staging-sjekkliste.py").read_text(
        encoding="utf-8")
    assert 'MOTORIMAGE = os.environ.get("WCAG_MOTORIMAGE") or MOTORIMAGE_TAG' \
        in sj
    assert '"{{.Id}}", MOTORIMAGE' in sj
    assert sj.count('"disponit-wcag-motor"') == 1, \
        "taggen skal bare stå som standardverdien for MOTORIMAGE"


def test_en_eksisterende_rullbakkrelease_maales_for_rullingen():
    """Codex' P1 (runde 6): registreringen av `--rullback-id` lå inne i en
    «finnes raden alt?»-test.

    Fantes raden, hoppet drillen over `registrer_release` helt — og dermed
    over den eneste sammenligningen mellom raden og bytene den mente å
    rulle tilbake til. Testen ga heller ingenting: `registrer_release`
    (014) er selv idempotent, og hever `release (…) er immutable` på
    avvikende innhold. Innpakningen fjernet bare porten.

    Og det som slapp forbi var destruktivt: `bytt_release` validerer kun
    kontrakten, så drillen ville drenert den levende deploymenten og
    flyttet registeret til den avvikende releasen — konflikten dukket
    først opp i fase 2, etter rullingen, med modulen uten claimende
    arbeider og drilltilstanden oppbrukt."""
    d = _drillskript()

    class _Base:
        """Registrerer kallene; `sprekk` hever på registrer_release."""

        def __init__(self, sprekk=None):
            self.kall, self.sprekk = [], sprekk
            self.rullet_tilbake = False

        def execute(self, sql, params=None):
            self.kall.append((sql, params))
            if self.sprekk and "registrer_release" in sql:
                raise RuntimeError(self.sprekk)
            return self

        def commit(self):
            self.kall.append(("COMMIT", None))

        def rollback(self):
            self.rullet_tilbake = True

    # Raden skrives UANSETT — ingen «finnes den?»-omvei foran.
    ok = _Base()
    d.registrer_drillrelease(ok, "wcag-r12", 1, "kh", "aa" * 32,
                             hva="rullback", flagg="--rullback-id")
    kall = [s for s, _ in ok.kall]
    assert not any("SELECT 1 FROM modulrelease" in s for s in kall), \
        "eksistenstesten er tilbake — da måles den eksisterende raden ikke"
    (reg,) = [p for s, p in ok.kall if "registrer_release" in s]
    assert reg[1] == "wcag-r12" and reg[5] == "aa" * 32, \
        "digesten må være forgjengerens, ikke den drilledes"
    assert reg[4] == d._manifest_hash(), \
        "manifesthashen må være utsjekkingens, som fase 2 regner den ut"
    assert "COMMIT" in kall and kall[-1] == "RESET ROLE"

    # Avvikende rad: drillen stopper HER, før noen `bytt_release`.
    sprakk = _Base(sprekk="release (m_wcag_audit,wcag-r12) er immutable")
    with pytest.raises(SystemExit) as ei:
        d.registrer_drillrelease(sprakk, "wcag-r12", 1, "kh", "aa" * 32,
                                 hva="rullback", flagg="--rullback-id")
    assert "immutabel" in str(ei.value) and "wcag-r12" in str(ei.value)
    assert "--rullback-id" in str(ei.value)
    assert sprakk.rullet_tilbake
    assert not any("bytt_release" in s for s, _ in sprakk.kall)

    # …og porten står før rullingen også i kilden.
    tekst = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    assert (tekst.index("registrer_drillrelease(m, a.rullback_id")
            < tekst.index("SELECT bytt_release"))


def test_en_eksisterende_kandidatrelease_maales_ogsaa_for_rullingen():
    """Codex' P2 (runde 7): porten foran drillen måler `moduldeployment`.

    En `--kandidat-id` som alt lå i `modulrelease` — uten deployment, med
    avvikende immutabelt innhold — ble derfor lest som «ubrukt». Drillen
    brukte da opp originaldeploymenten, bootet rullbakken og målte hele
    (a)/(b)/(b2), og konflikten dukket først opp i KANDIDATENS fase 2:
    fase 1 hadde alt stoppet rullbakk-arbeideren, så rullbakk-deploymenten
    sto claiming uten arbeider og begge drill-id-ene var konsumert.

    Kandidaten går nå samme vei som rullbakken (runde 6): raden skrives
    ubetinget FØR racet, og en avvikende rad stopper drillen der."""
    d = _drillskript()
    tekst = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    assert (tekst.index("registrer_drillrelease(m, a.kandidat_id")
            < tekst.index("SELECT bytt_release")), \
        "kandidatraden må skrives før den destruktive rullingen"

    class _Base:
        def __init__(self, sprekk=None):
            self.kall, self.sprekk = [], sprekk
            self.rullet_tilbake = False

        def execute(self, sql, params=None):
            self.kall.append((sql, params))
            if self.sprekk and "registrer_release" in sql:
                raise RuntimeError(self.sprekk)
            return self

        def commit(self):
            self.kall.append(("COMMIT", None))

        def rollback(self):
            self.rullet_tilbake = True

    # Kandidatens digest er den DRILLEDE releasens: fase 1/2 pinner det
    # lokalt bygde motorimaget, og drillen står på det hele veien.
    ok = _Base()
    d.registrer_drillrelease(ok, "wcag-r17", 1, "kh", "bb" * 32,
                             hva="kandidat", flagg="--kandidat-id")
    (reg,) = [p for s, p in ok.kall if "registrer_release" in s]
    assert reg[1] == "wcag-r17" and reg[5] == "bb" * 32
    assert reg[4] == d._manifest_hash()

    sprakk = _Base(sprekk="release (m_wcag_audit,wcag-r17) er immutable")
    with pytest.raises(SystemExit) as ei:
        d.registrer_drillrelease(sprakk, "wcag-r17", 1, "kh", "bb" * 32,
                                 hva="kandidat", flagg="--kandidat-id")
    assert "kandidat-releasen wcag-r17" in str(ei.value)
    assert "--kandidat-id" in str(ei.value)
    assert not any("bytt_release" in s for s, _ in sprakk.kall)


def test_kandidatens_oppdrag_bestilles_forst_naar_rullbakken_er_fenced(
        monkeypatch, tmp_path):
    """Codex' P2 (runde 6): `o3` ble bestilt FØR kandidatens fase 2.

    Rullbakk-arbeideren fra (b2) sto da levende og claimende, og kunne
    plukke og fullføre kandidatens oppdrag i vinduet før registerbyttet.
    Drillen kunne ikke se forskjell på det og en ekte overtakelse:
    `_vent_terminal` ble `utfort`, mens `_promoterte(o3, kandidat)` var
    tom. Utfallet var en rød drill, oppdaget først etter at BEGGE
    drill-id-ene var konsumert — livsløpet er enveis, så kjøringen var
    tapt på en ren kappløpstilfeldighet.

    Rekkefølgen ER fiksen, og den måles her: fase 2 (som fencer
    rullbakken) → bestillingen → fase 4/9 (som booter kandidaten).
    Oppdraget skal ligge og vente på nøyaktig den som overtar."""
    d = _drillskript()
    tekst = (ROT / "deploy/staging/rollback-m56.py").read_text(
        encoding="utf-8")
    fase2 = tekst.index('faser=("2",)')
    bestilling = tekst.index('_bestill_drill(sj, m, "framigjen")')
    fase49 = tekst.index('faser=("4", "9")')
    assert fase2 < bestilling < fase49, (
        "kandidatens oppdrag må bestilles ETTER registerbyttet og FØR"
        " arbeideren startes")
    # …og at rullbakken faktisk er ute av claiming MÅLES før bestillingen,
    # den antas ikke av at fase 2 returnerte 0.
    assert tekst.index("rb_livslop = _deployment") < bestilling
    assert 'if rb_livslop == "claiming"' in tekst

    # Utvalget må være ekte: `_kjor_faser` skal kjøre nøyaktig de fasene
    # den får, i rekkefølge — ellers er delingen over bare kosmetikk.
    kjorte = []

    def _fanget(cmd, **_kw):
        kjorte.append(cmd[cmd.index("--fase") + 1])
        return _Barn()

    monkeypatch.setattr(d.subprocess, "Popen", _fanget)
    monkeypatch.setattr(d, "PINNET_MOTORIMAGE", "sha256:" + "ab" * 32)
    evidens = tmp_path / "drill-evidens.jsonl"
    d._kjor_faser("r-kand", evidens, hva="kandidaten", faser=("2",))
    assert kjorte == ["2"]
    d._kjor_faser("r-kand", evidens, hva="kandidaten", faser=("4", "9"))
    assert kjorte == ["2", "4", "9"]
    # Rullbakken (b2) bootes fortsatt hel, i uendret rekkefølge.
    kjorte.clear()
    d._kjor_faser("r-rull", evidens, hva="rullbakken")
    assert kjorte == ["2", "4", "9"]


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

    Tre ting måles her: at kartet punkt→kilde er FULLSTENDIG og DISJUNKT
    mot kravpunktregisteret (et nytt punkt kan ikke stille arve nærmeste
    tall), at skriptets grenser og kildetyper er REGISTERETS (Codex P1,
    runde 15 — registeret eier dem nå, og et kall som sier noe annet
    avvises av basen, så et avvik her er en aksept som ville dødd på
    staging), og at et punkt uten ekte kilde BLOKKERER aksepten i stedet
    for å bli pyntet."""
    m = _aksept_skript()
    # NØYAKTIG punktregisterets INSERT: `akseptkrav_ci` (runde 16) bærer
    # også en `wcag-kontroll-v1`-rad med fem strengfelt, og den er et krav
    # til CI-KJØRINGEN, ikke et grensepunkt.
    sql = M049.read_text(encoding="utf-8")
    blokk = sql[sql.index("INSERT INTO akseptkrav_punkt"):]
    blokk = blokk[:blokk.index(";")]
    register = {
        p: (kt, g, mk) for p, kt, g, mk in re.findall(
            r"\('wcag-kontroll-v1',\s*'([^']+)',\s*'([^']+)',"
            r"\s*'([^']*)',\s*'([^']*)'\)", blokk)}
    punkter = set(register)
    assert len(punkter) == 21, punkter
    kilder = (set(m.MAALTE), set(m.CI_PUNKTER), set(m.UMAALTE),
              {"malautorisasjon.positiv_sti_virker"})
    flat = [p for s in kilder for p in s]
    assert len(flat) == len(set(flat)), "et punkt har to kilder"
    assert set(flat) == punkter, (
        f"kartet dekker ikke kravpunktregisteret: "
        f"{punkter ^ set(flat)}")
    # Grensene og kildetypene er registerets — skriptet gjentar dem.
    for punkt, (grense, _) in m.MAALTE.items():
        assert register[punkt][:2] == ("evidensfil", grense), \
            (punkt, register[punkt], grense)
    ci_grense, ci_maalt = "0 (porttest rød ved brudd)", \
        "0 (grønn CI på akseptcommiten)"
    for punkt in (*m.CI_PUNKTER, *m.UMAALTE):
        assert register[punkt] == ("ci_kjoring", ci_grense, ci_maalt), \
            (punkt, register[punkt])
    assert ci_grense in (ROT / "deploy/staging/m56-aksept.py").read_text(
        encoding="utf-8")
    assert register["malautorisasjon.positiv_sti_virker"] == \
        ("ci_kjoring", "ja", "ja")
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
def test_drillsonden_maaler_gjennom_porten_ikke_forbi_den(migrator):
    """Codex' P1 (runde 17): sonden og sjekklisten spurte basen DIREKTE.

    `kvitteringskapabiliteter` (005) er `REVOKE ALL … FROM PUBLIC` med
    kolonnegrant bare til `disponit_modul_eier`, og migrators medlemskap
    i eierrollen er `WITH INHERIT FALSE`. Spørringen dør derfor på
    «permission denied» — og i drillen dør den ETTER at rullingen har
    drenert den levende deploymenten og brukt opp rullbakk-ID-en, altså
    midt i en enveis måling som ikke kan gjentas.

    Målingen bor nå i `maal_rent_utfall`, bak fullmakten den trenger, og
    er den SAMME som `registrer_moduldrill` regner aksepten av."""
    k = _kjede(migrator)
    migrator.execute("RESET ROLE")
    # Formen som feilet: migrator når ikke tabellen selv.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute("SELECT count(*) FROM kvitteringskapabiliteter")
    migrator.rollback()

    # …og formen sonden bruker: deployfullmakten, gjennom porten.
    migrator.execute("SET ROLE disponit_modules_admin")
    assert migrator.execute("SELECT maal_rent_utfall(%s,%s)",
                            (k["ten"], k["opp"]["inflight"])).fetchone()[0] \
        is True
    migrator.execute("RESET ROLE")
    migrator.rollback()

    # Samme svar som drillraden bærer — én måling, ett sted.
    uten = k["oppdrag"](kapabilitet=False)
    migrator.commit()
    migrator.execute("SET ROLE disponit_modules_admin")
    assert migrator.execute("SELECT maal_rent_utfall(%s,%s)",
                            (k["ten"], uten)).fetchone()[0] is False
    # …og et oppdrag som ikke finnes er ikke NULL, det er nei.
    assert migrator.execute("SELECT maal_rent_utfall(%s, 0)",
                            (k["ten"],)).fetchone()[0] is False
    migrator.execute("RESET ROLE")
    migrator.rollback()

    # Fullmakten er lesing, ikke en skrivevei: kjøretidsrollen har den
    # ikke, og porten gir ingen vei inn i kapabilitetstabellen.
    rt = _rt()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT maal_rent_utfall('t',1)")
    finally:
        rt.rollback()
        rt.close()


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
              "head_sha": sha, "path": ".github/workflows/ci.yml",
              "event": "push", "head_branch": "main"}
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


def test_invariantpunktene_krever_kjoringen_paa_main_ikke_paa_forslaget():
    """Codex' P2 (runde 11): kjøringen ble målt på sti, conclusion og
    head_sha — ikke på HENDELSEN.

    `.github/workflows/ci.yml` trigges av BÅDE `pull_request` og push til
    `main`, så en grønn PR-kjøring passerte hver eneste kontroll. Kjøres
    akseptkommandoen fra et PR-utsjekk, holder `loes_akseptcommit` bare at
    commiten finnes lokalt — den krever ikke at den er nådd fra `main` — og
    da kunne en IMMUTABEL aksept skrives på bytes som aldri ble merget, og
    kanskje aldri blir det. Utrullingen skjer etter merge, så kjøringen
    aksepten hviler på skal være den historikken faktisk fikk.
    """
    m = _aksept_skript()
    sha = "a" * 40
    # Forutsetningen porten hviler på: workflowen kjører faktisk for begge
    # hendelsene, så `event` er ikke utledbart av stien.
    ci = (ROT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pull_request:" in ci and "branches: [main]" in ci

    groenn = {"id": 42, "status": "completed", "conclusion": "success",
              "head_sha": sha, "path": ".github/workflows/ci.yml",
              "event": "push", "head_branch": "main"}
    assert m._vurder_ci_kjoring(groenn, "42", sha) == []
    for muteres, ord_i_feil in (
            ({"event": "pull_request"}, "ikke push"),
            ({"event": None}, "ikke push"),
            ({"event": "workflow_dispatch"}, "ikke push"),
            ({"head_branch": "m56-akseptflipp"}, "skal være merget"),
            ({"head_branch": None}, "skal være merget")):
        feil = m._vurder_ci_kjoring(dict(groenn, **muteres), "42", sha)
        assert any(ord_i_feil in f for f in feil), (muteres, feil)


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
